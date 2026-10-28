"""engine.py: from a pasted list of DOIs to verified, filed PDFs and a report.

For each DOI:  facts (registry) -> twins -> free copies -> up to 3 rounds of
download + check -> PDFs\\ or PDFs\\Check these\\ -> journal, report, manifest.
No browser and no other application is ever opened by this module.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

import sources as S
import verify as V

STATUSES = ['Verified', 'Already verified', 'Check these', 'Not downloadable automatically',
            'No free legal copy', 'Not checkable: scanned', 'DOI not registered', 'Not checked',
            'Duplicate', 'Not a DOI', 'Stopped', 'Available', 'Not available']
COLUMNS = ['No.', 'DOI', 'Status', 'Retracted', 'Proof', 'Version', 'Source', 'Round 1', 'Round 2',
           'Round 3', 'Sources tried', 'File', 'Pages', 'Registry pages', 'Title (registry)',
           'First author', 'Note', 'Pasted']
MAX_ROUNDS = 3
REFETCH_WAIT = 15
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>]+', re.I)
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
VERSION_SUFFIX = {'publishedVersion': '', 'acceptedVersion': '_accepted-manuscript',
                  'submittedVersion': '_preprint', '': '_copy'}


# ------------------------------------------------------------------ input

def clean_doi(d: str) -> str:
    d = re.split(r'[?#]', d, maxsplit=1)[0]
    while True:
        before = d
        d = d.rstrip('.,;:\'"')
        for o, c in ('()', '[]', '{}'):
            if d.endswith(c) and d.count(o) < d.count(c):
                d = d[:-1]
        if d == before:
            return d


def parse_input(text: str) -> list[dict]:
    """One item per DOI found; one 'Not a DOI' item per non-blank line holding none."""
    items, first = [], {}
    for n, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith('#'):
            continue
        found = [clean_doi(m.group(0)) for m in DOI_RE.finditer(urllib.parse.unquote(raw))]
        if not found:
            items.append({'line': n, 'input': raw, 'doi': '', 'dup_of': None})
            continue
        for d in found:
            key = V.norm_doi(d)
            items.append({'line': n, 'input': raw, 'doi': key, 'dup_of': first.get(key)})
            first.setdefault(key, n)
    return items


# ------------------------------------------------------------------ names

def file_name(template: str, facts: V.Facts | None, doi: str, version: str, retracted: bool, taken: set) -> str:
    f = facts or V.Facts(doi=doi, title='')
    author = (''.join(V.words(f.authors[0])) if f.authors and V.words(f.authors[0]) else 'unknown').capitalize()
    title = ' '.join(V.clean_registry_text(f.title).split()[:8])[:70] or doi.replace('/', '_')
    stem = (template or '{author}_{year}_{title}').format(author=author, year=f.year or 'n.d.', title=title,
                                                           doi=doi.replace('/', '_'))
    stem = BAD_CHARS.sub('', stem)
    stem = re.sub(r'\s+', '-', stem).strip(' .-_')[:110] or 'paper'
    stem += VERSION_SUFFIX.get(version, '_copy')
    if retracted:
        stem += '_RETRACTED'
    if stem.upper() in RESERVED:
        stem += '_'
    name, i = stem + '.pdf', 2
    while name.lower() in taken:
        name, i = f'{stem}_{i}.pdf', i + 1
    taken.add(name.lower())
    return name


# ------------------------------------------------------------------ the engine

@dataclass
class RoundResult:
    label: str
    url: str
    verdict: V.Verdict
    sha256: str
    data: bytes = field(repr=False, default=b'')
    version: str = ''
    published: bool = False

    def score(self) -> int:
        v = self.verdict
        return (4 if v.ok else 0) + (2 if v.title_found else 0) + (1 if v.author_found else 0) + (1 if v.self_doi else 0)


class Engine:
    def __init__(self, folder: Path, email: str, keys: dict | None = None, template: str = '',
                 resolvers: list | None = None, web: S.Web | None = None, pacer: S.Pacer | None = None,
                 sleep=time.sleep, stop=None, on_row=None, say=None):
        self.folder = Path(folder)
        self.pdf_dir = self.folder / 'PDFs'
        self.check_dir = self.pdf_dir / 'Check these'
        self.journal_path = self.folder / 'download_log.json'
        self.manifest_path = self.folder / 'Manifest.csv'
        self.email, self.template = email, template or '{author}_{year}_{title}'
        self.web = web or S.Web(email)
        self.client = S.Client(self.web, pacer or S.Pacer(sleep), sleep)
        self.sleep = sleep
        self.registry = S.Registry(self.client, email)
        self.finder = S.Finder(self.client, self.registry, email, keys, resolvers)
        self.stop = stop or (lambda: False)
        self.on_row = on_row or (lambda row: None)
        self.say = say or (lambda msg: None)
        self.journal = self._load_journal()

    # ---- journal (resume) and manifest
    def _load_journal(self) -> dict:
        try:
            return json.loads(self.journal_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save_journal(self):
        self.journal_path.write_text(json.dumps(self.journal, indent=1), encoding='utf-8')

    def _manifest(self, doi, name, url, rnd, proof, version, sha):
        new = not self.manifest_path.exists()
        with open(self.manifest_path, 'a', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['DOI', 'File', 'Source URL', 'Round', 'Proof', 'Version', 'sha256', 'Date'])
            w.writerow([doi, name, url, rnd, proof, version, sha, time.strftime('%Y-%m-%d %H:%M')])

    # ---- the run
    def run(self, items: list[dict], check_only: bool = False) -> list[dict]:
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.check_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for i, it in enumerate(items, 1):
            row = {c: '' for c in COLUMNS}
            row.update({'No.': i, 'DOI': it['doi'], 'Pasted': it['input']})
            rows.append(row)
        unique = sum(1 for it in items if it['doi'] and it['dup_of'] is None)
        k = 0
        for it, row in zip(items, rows):
            if not it['doi']:
                row.update(Status='Not a DOI', Note='no DOI found on this line')
            elif it['dup_of']:
                row.update(Status='Duplicate', Note=f'same DOI as line {it["dup_of"]}')
            elif self.stop():
                row.update(Status='Stopped', Note='stopped before this one')
            else:
                k += 1
                try:
                    self._one(it['doi'], row, check_only)
                except Exception as e:                      # one bad DOI must not stop the rest
                    row.update(Status='Not checked', Note=f'{type(e).__name__}: {e}')
                self.say(f'[{k}/{unique}] {row["Status"]:<32} {row["File"] or row["Note"][:70]}')
            self.on_row(row)
        return rows

    def _one(self, doi: str, row: dict, check_only: bool):
        prev = self.journal.get(doi)
        if prev and prev.get('status') == 'Verified' and (self.pdf_dir / prev.get('file', '')).is_file() and not check_only:
            data = (self.pdf_dir / prev['file']).read_bytes()
            if hashlib.sha256(data).hexdigest() == prev.get('sha256'):
                row.update(Status='Already verified', File=prev['file'], Proof=prev.get('proof', ''),
                           Version=prev.get('version', ''), Source=prev.get('source', ''),
                           **{'Title (registry)': prev.get('title', ''), 'First author': prev.get('author', '')})
                return
        status, facts, note = self.registry.facts(doi)
        if status == 'not_registered':
            row.update(Status='DOI not registered', Note=note)
            return
        if status != 'ok':
            row.update(Status='Not checked', Note=f'registry unreachable: {note}; run again')
            return
        facts.twins = self.registry.twins(facts)
        row.update(**{'Title (registry)': facts.title, 'First author': facts.authors[0] if facts.authors else '',
                      'Registry pages': f'{facts.page_first}-{facts.page_last}' if facts.page_first else ''})
        row['Retracted'] = 'RETRACTED' if facts.retracted else ''
        self.journal.setdefault(doi, {}).update(title=facts.title, author=row['First author'], year=facts.year,
                                                venue=facts.venue, authors=facts.authors, retracted=facts.retracted)
        cands, notes = self.finder.candidates(facts)
        if facts.twins:
            notes.append('same-title twin(s): ' + ', '.join(facts.twins))
        if check_only:
            row['Sources tried'] = '; '.join(notes)
            row.update(Status='Available' if cands else 'Not available',
                       Source='; '.join(f'{c.label}: {c.url}' for c in cands[:4]),
                       Note='check only, nothing downloaded')
            return
        if not cands:
            if S.NEVER_ASK_REASON in notes:
                row.update(Status='Not downloadable automatically', Note=S.NEVER_ASK_REASON)
            elif any('service busy' in n or 'needs an email' in n for n in notes):
                row.update(Status='Not checked', Note='; '.join(notes) + '; run again')
            else:
                row.update(Status='No free legal copy', Note='ask your library or the author (request text ready)')
            row['Sources tried'] = '; '.join(notes)
            self.journal[doi]['status'] = row['Status']
            self._save_journal()
            return

        results: list[RoundResult] = []
        seen_hashes: set[str] = set()
        rnd, idx, refetched = 0, 0, set()
        while rnd < MAX_ROUNDS and idx < len(cands):
            if self.stop():
                break
            c = cands[idx]
            r = self.finder.fetch(c)
            if r.status != 200 or not r.is_pdf:
                notes.append(f'{c.label}: {r.reason or ("HTTP " + str(r.status))}')
                idx += 1
                continue
            sha = hashlib.sha256(r.data).hexdigest()
            rnd += 1
            if sha in seen_hashes:
                notes.append(f'round {rnd}: {c.label}: same file again, no new evidence')
                row[f'Round {rnd}'] = 'same file again'
                idx += 1
                continue
            seen_hashes.add(sha)
            verdict = V.verify(r.data, facts, published=c.published, content_length=r.content_length)
            res = RoundResult(c.label, r.url, verdict, sha, r.data, c.version, c.published)
            results.append(res)
            row[f'Round {rnd}'] = (f'{verdict.proof}: {c.label}' if verdict.ok else f'fail ({verdict.reason}): {c.label}')
            if verdict.ok:
                break
            if verdict.reason_code == 'damaged' and c.key() not in refetched:
                refetched.add(c.key())                # one fresh download of the same copy
                self.sleep(REFETCH_WAIT)
                continue
            idx += 1
        # no other source: fetch the same copy again to fill the rounds honestly
        while rnd < MAX_ROUNDS and results and not results[-1].verdict.ok and idx >= len(cands) and not self.stop():
            last = cands[-1]
            self.sleep(REFETCH_WAIT)
            r = self.finder.fetch(last)
            rnd += 1
            if r.status == 200 and r.is_pdf and hashlib.sha256(r.data).hexdigest() not in seen_hashes:
                verdict = V.verify(r.data, facts, published=last.published, content_length=r.content_length)
                results.append(RoundResult(last.label, r.url, verdict, hashlib.sha256(r.data).hexdigest(), r.data,
                                           last.version, last.published))
                row[f'Round {rnd}'] = (f'{verdict.proof}: {last.label}' if verdict.ok else f'fail ({verdict.reason}): {last.label}')
                if verdict.ok:
                    break
            else:
                row[f'Round {rnd}'] = 'same file again' if r.status == 200 and r.is_pdf else f'no file: {r.reason or r.status}'
        row['Sources tried'] = '; '.join(notes)
        passed = next((x for x in results if x.verdict.ok), None)
        if passed:
            self._file(doi, facts, passed, row, rnd, 'PDFs')
            # a publisher file that failed while a later copy passed is kept for comparison
            for x in results:
                if x is not passed and x.published:
                    self._file(doi, facts, x, row, rnd, 'check', extra_note='publisher file did not match the registry; please compare')
            return
        if not results:
            if any('web page, not a file' in n or 'paused' in n or 'HTTP 403' in n for n in notes):
                row.update(Status='Not downloadable automatically', Note='the site gives no file to programs')
            elif any('service busy' in n for n in notes):
                row.update(Status='Not checked', Note='service busy; run again later')
            else:
                row.update(Status='Not downloadable automatically', Note='no source gave a file')
            self.journal[doi]['status'] = row['Status']
            self._save_journal()
            return
        best = max(results, key=lambda x: x.score())
        if best.verdict.reason_code == 'no_text':
            row['Status'] = 'Not checkable: scanned'
        else:
            row['Status'] = 'Check these'
        row['Note'] = ' | '.join(f'R{i + 1}: {x.verdict.reason}' for i, x in enumerate(results))
        self._file(doi, facts, best, row, rnd, 'check')

    def _file(self, doi, facts, res: RoundResult, row, rnd, where, extra_note=''):
        target = self.pdf_dir if where == 'PDFs' else self.check_dir
        taken = {p.name.lower() for p in target.iterdir()}
        name = file_name(self.template, facts, doi, res.version, facts.retracted, taken)
        (target / name).write_bytes(res.data)
        if where == 'PDFs':
            row.update(Status='Verified', Proof=res.verdict.proof, Version=res.version or 'unknown',
                       Source=res.label, File=name, Pages=res.verdict.pages,
                       Note=(res.verdict.page_note + ('; ' + '; '.join(res.verdict.notes) if res.verdict.notes else '')).strip('; '))
            self.journal[doi].update(status='Verified', file=name, sha256=res.sha256, proof=res.verdict.proof,
                                     version=res.version, source=res.label, url=res.url)
            self._manifest(doi, name, res.url, rnd, res.verdict.proof, res.version, res.sha256)
        else:
            if row.get('Status') != 'Verified':
                row.update(File='Check these\\' + name, Pages=res.verdict.pages, Version=res.version or 'unknown', Source=res.label)
                self.journal[doi].update(status=row['Status'], file='Check these\\' + name, sha256=res.sha256,
                                         reason=row['Note'])
            if extra_note:
                row['Note'] = (row['Note'] + '; ' + extra_note).strip('; ')
            self._manifest(doi, 'Check these\\' + name, res.url, rnd, 'none', res.version, res.sha256)
        self._save_journal()

    # ---- after the run
    def recheck(self) -> dict:
        """Only the files this program filed. Returns counts and the problems found."""
        out = {'verified': 0, 'check_these': 0, 'missing': [], 'changed': []}
        for doi, j in self.journal.items():
            f = j.get('file')
            if not f:
                continue
            p = self.pdf_dir / f
            if not p.is_file():
                out['missing'].append(f)
                continue
            if hashlib.sha256(p.read_bytes()).hexdigest() != j.get('sha256'):
                out['changed'].append(f)
                continue
            out['verified' if j.get('status') == 'Verified' else 'check_these'] += 1
        return out


# ------------------------------------------------------------------ report, PRISMA, exports, letters

def write_report(rows: list[dict], path: Path) -> Path:
    try:
        f = open(path, 'w', newline='', encoding='utf-8-sig')
    except PermissionError:
        path = path.with_name(f'Report {time.strftime("%Y-%m-%d %H%M%S")}.csv')
        f = open(path, 'w', newline='', encoding='utf-8-sig')
    with f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    return path


def totals_agree(items: list[dict], rows: list[dict]) -> bool:
    return len(rows) == len(items) and all(r['Status'] in STATUSES for r in rows)


def prisma(rows: list[dict]) -> dict:
    unique = [r for r in rows if r['DOI'] and r['Status'] not in ('Duplicate',)]
    got = [r for r in unique if r['Status'] in ('Verified', 'Already verified')]
    missing = Counter(r['Status'] for r in unique if r not in got)
    return {'sought': len(unique), 'retrieved': len(got), 'not_retrieved': len(unique) - len(got),
            'reasons': dict(missing)}


def export_references(journal: dict, folder: Path) -> tuple[Path, Path]:
    ris, bib = [], []
    for doi, j in journal.items():
        if j.get('status') != 'Verified':
            continue
        authors = j.get('authors') or ([j['author']] if j.get('author') else [])
        ris += ['TY  - JOUR'] + [f'AU  - {a}' for a in authors] + [f'TI  - {j.get("title", "")}',
                f'PY  - {j.get("year", "")}', f'JO  - {j.get("venue", "")}', f'DO  - {doi}', 'ER  - ', '']
        key = re.sub(r'[^a-z0-9]', '', (authors[0] if authors else 'anon').lower()) + str(j.get('year', ''))
        bib.append(f'@article{{{key},\n  author = {{{" and ".join(authors)}}},\n  title = {{{j.get("title", "")}}},\n'
                   f'  journal = {{{j.get("venue", "")}}},\n  year = {{{j.get("year", "")}}},\n  doi = {{{doi}}}\n}}\n')
    pr, pb = folder / 'References.ris', folder / 'References.bib'
    pr.write_text('\n'.join(ris), encoding='utf-8')
    pb.write_text('\n'.join(bib), encoding='utf-8')
    return pr, pb


def request_texts(row: dict, your_name: str = '[your name]') -> dict:
    title, doi = row.get('Title (registry)') or '[title]', row.get('DOI', '')
    author = f"""Subject: Request for a copy of your article: {title}

Dear Dr. {row.get('First author') or '[author]'},

I am {your_name}, conducting a systematic review for non-commercial academic research. I have been unable to obtain a copy of your article "{title}" (DOI {doi}) through the sources available to me.

Would you be willing to share a copy for my own reading and data extraction? I will not redistribute it.

Thank you for your time.
{your_name}"""
    library = f"""Request for inter-library loan / document delivery

Title: {title}
DOI: {doi}
First author: {row.get('First author') or ''}
Purpose: full-text screening for a systematic review (non-commercial research)
Requested by: {your_name}"""
    return {'author': author, 'library': library}


def acm_letter(dois: list[str], your_name: str = '[your name]', institution: str = '[your institution]',
               review_title: str = '[your review title]') -> str:
    return f"""To: dl-info@hq.acm.org
Cc: permissions@acm.org
Subject: Request for text and data mining access: systematic review ({len(dois)} ACM DOIs)

Dear ACM,

I am {your_name} at {institution}, conducting a systematic review titled "{review_title}" for non-commercial academic research.

My screening identified {len(dois)} papers in the ACM Digital Library, listed by DOI below. I would like permission to retrieve their full-text PDFs with a small research tool I wrote, DOI-Downloader, which identifies itself with my contact email in every request, requests only the DOIs listed, one at a time, and keeps the files for my own screening and data extraction without redistributing them.

If you prefer another route, such as an API, a dataset, or an institution's ACM Premium access, I will gladly use it.

DOIs:
""" + '\n'.join(dois) + f"""

Thank you for your consideration.
{your_name}"""
