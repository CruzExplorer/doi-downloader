"""verify.py: is this PDF the right paper, and is it complete?

Pure file checking. Nothing here touches the network. The facts to compare
against (title, authors, pages, type) come from the DOI registry, never from
the file itself.

Results:
    Verdict(ok=True,  proof='PASS-A' | 'PASS-B', ...)
    Verdict(ok=False, reason='...')        with reason_code in FAIL_CODES
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)

GENERIC_TITLES = {
    'editorial', 'preface', 'introduction', 'correction', 'erratum', 'corrigendum',
    'retraction', 'foreword', 'commentary', 'letter to the editor', 'abstract',
    'poster', 'keynote', 'panel', 'workshop', 'tutorial', 'front matter',
}
NOTICE_WORDS = re.compile(
    r'\b(correction to|corrigendum to|erratum to|erratum:|retraction of|retraction notice|'
    r'retracted article|expression of concern)\b', re.I)
FAIL_CODES = {
    'no_text', 'no_title', 'other_doi', 'twin', 'notice', 'no_author', 'generic_title',
    'twin_exists', 'damaged', 'pages', 'too_short',
}


# ------------------------------------------------------------------ normalisers

# Spacing accents that LaTeX-made PDFs leave next to a letter ("Garc´ıa"): dropped.
_ACCENTS = {ord(c): None for c in (
    '´'   # acute accent
    '`'   # grave accent
    '¨'   # diaeresis
    'ˆ'   # modifier circumflex
    '˜'   # small tilde
    '¯'   # macron
    '˘'   # breve
    '˙'   # dot above
    '˚'   # ring above
    '˝'   # double acute
    '¸'   # cedilla
)}
# Every dash form a DOI may be printed with: all become '-'.
_DASHES = str.maketrans({c: '-' for c in (
    '‐'   # hyphen
    '‑'   # non-breaking hyphen
    '‒'   # figure dash
    '–'   # en dash
    '—'   # em dash
    '―'   # horizontal bar
    '−'   # minus sign
)})
# Invisible characters that split a DOI or a word without showing: removed.
_ZERO_WIDTH = str.maketrans({c: None for c in (
    '­'   # soft hyphen
    '​'   # zero width space
    '‌'   # zero width non-joiner
    '‍'   # zero width joiner
    '﻿'   # zero width no-break space (BOM)
)})


def clean_registry_text(s: str) -> str:
    """Strip JATS/HTML markup and entities that Crossref leaves in titles."""
    s = re.sub(r'<[^>]+>', ' ', s or '')
    s = html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def words(s: str) -> list[str]:
    """Title/name normaliser: NFKD, accents dropped, dotless i -> i, lower case,
    letters and digits as whole words. 'Garc´ıa' and 'García' both give ['garcia']."""
    s = clean_registry_text(s).translate(_ACCENTS)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace('ı', 'i').replace('Ł', 'L').replace('ł', 'l')
    s = s.replace('Ø', 'O').replace('ø', 'o').replace('ß', 'ss')
    return re.findall(r'[a-z0-9]+', s.lower())


def norm_doi(d: str) -> str:
    d = unicodedata.normalize('NFKD', d or '').translate(_DASHES).translate(_ZERO_WIDTH)
    return d.strip().lower()


def doi_pattern(doi: str) -> re.Pattern:
    """Boundary match: '…_5' never matches '…_52'; a prefix never matches a longer DOI."""
    return re.compile(r'(?<![0-9a-z._/-])' + re.escape(norm_doi(doi)) + r'(?![0-9a-z_/-]|\.[0-9a-z])')


def join_wrapped_dois(text: str) -> str:
    """A DOI cut by a line wrap is joined only after . / - _ ; otherwise the break is a space."""
    text = text.translate(_DASHES).translate(_ZERO_WIDTH)
    text = re.sub(r'([./_-])\n[ \t]*', r'\1', text)
    return text.replace('\n', ' ')


DOI_ANY = re.compile(r'10\.\d{4,9}/[^\s"<>()\[\]{}]+', re.I)


def dois_in(text: str) -> list[str]:
    found = []
    for m in DOI_ANY.finditer(join_wrapped_dois(text)):
        d = norm_doi(m.group(0)).rstrip('.,;:')
        if d not in found:
            found.append(d)
    return found


# ------------------------------------------------------------------ page text

@dataclass
class PageText:
    lines: list[tuple[float, str]]          # (font size, text) for horizontal lines
    header: str                             # top 12% of the page
    footer: str                             # bottom 12% of the page
    body: str                               # everything, in reading order
    word_count: int
    has_image: bool


def read_page(page: pymupdf.Page) -> PageText:
    d = page.get_text('dict', sort=True)
    h = page.rect.height or 1.0
    lines, header, footer, body = [], [], [], []
    for block in d.get('blocks', []):
        if block.get('type') != 0:
            continue
        for ln in block.get('lines', []):
            dx, dy = ln.get('dir', (1, 0))
            if abs(dy) > 0.2:                       # vertical stamps (arXiv margin) are skipped
                continue
            spans = ln.get('spans', [])
            text = ''.join(s.get('text', '') for s in spans).strip()
            if not text:
                continue
            size = max((s.get('size', 0) for s in spans), default=0)
            lines.append((size, text))
            body.append(text)
            y = ln.get('bbox', (0, 0, 0, 0))[1]
            if y < 0.12 * h:
                header.append(text)
            elif y > 0.88 * h:
                footer.append(text)
    full = '\n'.join(body)
    return PageText(lines, '\n'.join(header), '\n'.join(footer), full,
                    len(re.findall(r'\w+', full)), bool(page.get_images(full=False)))


# ------------------------------------------------------------------ facts

@dataclass
class Facts:
    doi: str
    title: str
    subtitle: str = ''
    authors: list[str] = field(default_factory=list)     # family names, in order
    year: str = ''
    page_first: int | None = None
    page_last: int | None = None
    kind: str = ''                                        # Crossref 'type'
    twins: list[str] = field(default_factory=list)        # other DOIs with the same title+author
    arxiv_id: str = ''
    retracted: bool = False
    venue: str = ''
    abstract: str = ''

    @property
    def page_range(self) -> int | None:
        if self.page_first and self.page_last and self.page_last > self.page_first:
            return self.page_last - self.page_first + 1
        return None

    def title_candidates(self) -> list[list[str]]:
        t = words(self.title)
        cands = [t]
        if self.subtitle:
            cands.append(t + words(self.subtitle))
        return [c for c in cands if c]


# ------------------------------------------------------------------ verdict

@dataclass
class Verdict:
    ok: bool
    proof: str = ''                 # 'PASS-A' or 'PASS-B'
    reason: str = ''
    reason_code: str = ''
    pages: int = 0
    cover_sheet: bool = False
    title_found: bool = False
    author_found: bool = False
    self_doi: str = ''              # the DOI printed in self position, if any
    page_note: str = ''
    damage: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    if n == 0 or n > len(seq):
        return False
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def title_block(pt: PageText) -> list[str]:
    """The largest-font lines on the page, merged while they stay large."""
    if not pt.lines:
        return []
    sizes = sorted((s for s, _ in pt.lines), reverse=True)
    top = sizes[0]
    body_median = sizes[len(sizes) // 2]
    floor = max(top - 0.5, body_median + 2.0)
    block, started = [], False
    for size, text in pt.lines:
        if size >= floor:
            block.append(text)
            started = True
        elif started and size >= top - 2.5 and len(block) < 6:
            block.append(text)                  # a second title line in a slightly smaller face
        elif started:
            break
    return block


def title_in_block(facts: Facts, block: list[str]) -> bool:
    bw = words(' '.join(block))
    if not bw:
        return False
    for cand in facts.title_candidates():
        if _contains(bw, cand) and len(cand) >= 0.75 * len(bw):
            return True
    return False


def author_on_page(facts: Facts, pt: PageText) -> bool:
    text_words = words(pt.body)
    if not facts.authors:
        return False
    first = words(facts.authors[0])
    if not first:
        return False
    fam = first[-1]
    ok = fam in text_words
    if ok and len(fam) <= 2 and len(facts.authors) > 1:
        second = words(facts.authors[1])
        ok = bool(second) and second[-1] in text_words
    return ok


def self_dois(pt: PageText, meta: dict, xmp: str) -> list[str]:
    found = dois_in(pt.header) + dois_in(pt.footer)
    for v in (meta or {}).values():
        if isinstance(v, str):
            found += dois_in(v)
    found += dois_in(xmp or '')
    out = []
    for d in found:
        if d not in out:
            out.append(d)
    return out


# ------------------------------------------------------------------ completeness

def damage_report(data: bytes, content_length: int | None = None) -> list[str]:
    problems = []
    if not data[:1024].lstrip().startswith(b'%PDF-'):
        problems.append('no PDF signature')
        return problems
    if b'%%EOF' not in data[-1024:]:
        problems.append('file end marker missing (cut off?)')
    if content_length and len(data) < content_length:
        problems.append(f'received {len(data):,} of {content_length:,} bytes')
    try:
        doc = pymupdf.open(stream=data, filetype='pdf')
    except Exception as e:
        problems.append(f'will not open: {type(e).__name__}')
        return problems
    with doc:
        if doc.needs_pass:
            problems.append('password-locked')
            return problems
        if doc.page_count < 2:
            problems.append(f'only {doc.page_count} page')
        pymupdf.TOOLS.reset_mupdf_warnings()
        blank = []
        for i, page in enumerate(doc):
            try:
                page.get_pixmap(dpi=20)
                txt = page.get_text()
            except Exception as e:
                problems.append(f'page {i + 1} will not render: {type(e).__name__}')
                continue
            if not txt.strip() and not page.get_images(full=False):
                blank.append(i + 1)
        warn = pymupdf.TOOLS.mupdf_warnings()
        if re.search(r'error|repair|syntax|treating as end of file', warn or '', re.I):
            problems.append('damaged content: ' + warn.strip().splitlines()[0][:120])
        middle_blank = [p for p in blank if 1 < p < doc.page_count]
        if middle_blank:
            problems.append(f'blank page(s) in the middle: {middle_blank[:5]}')
    return problems


def page_count_note(n_pages: int, facts: Facts, published: bool, cover: bool) -> tuple[bool, str]:
    """(ok, note). ok is False only when the registry gives a real range and we miss it."""
    rng = facts.page_range
    if rng is None:
        return True, 'page count: not checkable from the registry'
    if published:
        lo, hi = rng - 1, rng + (3 if cover else 2)
        ok = lo <= n_pages <= hi
        return ok, f'pages {n_pages} vs registry {rng} ({"ok" if ok else "mismatch"})'
    ok = 0.5 * rng <= n_pages <= 2 * rng
    return ok, f'pages {n_pages} vs registry {rng} (preprint/accepted, {"ok" if ok else "mismatch"})'


# ------------------------------------------------------------------ the check

def verify(data: bytes, facts: Facts, *, published: bool = True,
           content_length: int | None = None) -> Verdict:
    v = Verdict(ok=False)
    v.damage = damage_report(data, content_length)
    if any(p.startswith(('no PDF signature', 'will not open', 'password')) for p in v.damage):
        v.reason, v.reason_code = 'damaged: ' + '; '.join(v.damage), 'damaged'
        return v
    try:
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            v.pages = doc.page_count
            pages = [read_page(doc[i]) for i in range(min(3, doc.page_count))]
            meta = doc.metadata or {}
            try:
                xmp = doc.get_xml_metadata() or ''
            except Exception:
                xmp = ''
    except Exception as e:
        v.damage.append(f'will not open: {type(e).__name__}')
        v.reason, v.reason_code = 'damaged: ' + '; '.join(v.damage), 'damaged'
        return v
    serious = [p for p in v.damage if not p.startswith('only')]
    if serious:                                   # a damaged file needs a fresh copy, whatever it says
        v.reason, v.reason_code = 'damaged: ' + '; '.join(serious), 'damaged'
        return v
    if not pages or all(p.word_count == 0 for p in pages[:2]):
        v.reason, v.reason_code = 'not checkable: scanned (no text on pages 1-2)', 'no_text'
        return v

    # cover sheet (ACM "Latest updates" page, repository cover): the title is on
    # page 2 as well, or page 1 is a near-empty sheet without the title block
    first = 0
    if len(pages) >= 2:
        t1 = title_in_block(facts, title_block(pages[0]))
        t2 = title_in_block(facts, title_block(pages[1]))
        if (t1 and t2) or (t2 and not t1 and pages[0].word_count < 150):
            first, v.cover_sheet = 1, True
    content = pages[first]
    window = pages[first:first + 2]

    v.title_found = title_in_block(facts, title_block(content))
    v.author_found = author_on_page(facts, content)
    printed = []
    for p in window:
        for d in self_dois(p, meta, xmp):
            if d not in printed:
                printed.append(d)
    target = norm_doi(facts.doi)
    has_target = target in printed                       # exact token, never a prefix
    others = [d for d in printed if d != target]
    twins = {norm_doi(t) for t in facts.twins}
    v.self_doi = target if has_target else (others[0] if others else '')

    # notices and wrong papers
    head = '\n'.join(window[0].lines[i][1] for i in range(min(12, len(window[0].lines))))
    if NOTICE_WORDS.search(head) and facts.kind not in ('correction', 'retraction'):
        v.reason, v.reason_code = 'this is a correction/retraction notice, not the paper', 'notice'
        return v
    if any(d in twins for d in printed):
        v.reason, v.reason_code = f'this is the twin {next(d for d in printed if d in twins)}', 'twin'
        return v
    if not v.title_found:
        if others and not has_target:
            v.reason, v.reason_code = f'wrong paper: prints DOI {others[0]}', 'other_doi'
        else:
            v.reason, v.reason_code = 'registry title not found in the title block', 'no_title'
        return v

    # completeness
    if v.pages < 2:
        v.reason, v.reason_code = 'only 1 page (cover or abstract?)', 'too_short'
        return v
    pages_ok, v.page_note = page_count_note(v.pages, facts, published, v.cover_sheet)
    if not pages_ok:
        v.reason, v.reason_code = v.page_note, 'pages'
        return v

    # PASS-A: title + target DOI in self position + no other self DOI
    if has_target and not others:
        v.ok, v.proof = True, 'PASS-A'
        return v
    if has_target and others:
        v.notes.append(f'other DOI also printed: {others[0]}')
    # PASS-B
    if others and not has_target:
        v.reason, v.reason_code = f'wrong paper: prints DOI {others[0]}', 'other_doi'
        return v
    if facts.twins:
        v.reason, v.reason_code = ('a same-title twin exists; only a printed DOI can prove it: '
                                   + ', '.join(facts.twins)), 'twin_exists'
        return v
    tw = words(facts.title)
    if len(tw) < 4 or ' '.join(tw) in GENERIC_TITLES:
        v.reason, v.reason_code = 'title too short/generic to prove identity without a DOI', 'generic_title'
        return v
    if not v.author_found:
        v.reason, v.reason_code = 'first author not found on the first content page', 'no_author'
        return v
    v.ok, v.proof = True, 'PASS-B'
    return v


def first_page_hints(data: bytes) -> tuple[list[str], str]:
    """For Import: the DOIs printed on page 1-2 and the page-1 title block text."""
    try:
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            pts = [read_page(doc[i]) for i in range(min(2, doc.page_count))]
            meta = doc.metadata or {}
            try:
                xmp = doc.get_xml_metadata() or ''
            except Exception:
                xmp = ''
    except Exception:
        return [], ''
    dois = []
    for p in pts:
        for d in self_dois(p, meta, xmp) + dois_in(p.body):
            if d not in dois:
                dois.append(d)
    return dois, ' '.join(title_block(pts[0])) if pts else ''
