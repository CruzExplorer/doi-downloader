"""importer.py: files the user obtained another way (library, ACM bulk download,
authors, their own earlier downloads) are matched to the DOIs in the list and
given the same check, then filed. Unmatched files are left exactly where they are.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import verify as V
from engine import Engine, RoundResult


def gather(paths: list[Path]) -> list[tuple[str, bytes]]:
    """(name, bytes) for every PDF among the given files and ZIP archives. Reads only."""
    out = []
    for p in paths:
        p = Path(p)
        if p.suffix.lower() == '.pdf' and p.is_file():
            out.append((p.name, p.read_bytes()))
        elif p.suffix.lower() == '.zip' and p.is_file():
            try:
                with zipfile.ZipFile(p) as z:
                    for info in z.infolist():
                        if info.filename.lower().endswith('.pdf') and not info.is_dir():
                            out.append((f'{p.name}/{Path(info.filename).name}', z.read(info)))
            except zipfile.BadZipFile:
                out.append((p.name, b''))
    return out


def match_doi(data: bytes, dois: list[str], facts_by_doi: dict) -> str:
    """The DOI in the list this file belongs to, or ''. By the DOI printed in the file,
    else by the registry title appearing in the file's title block."""
    printed, block = V.first_page_hints(data)
    wanted = {V.norm_doi(d) for d in dois}
    for d in printed:
        if d in wanted:
            return d
    bw = V.words(block)
    if not bw:
        return ''
    for d in dois:
        f = facts_by_doi.get(d)
        if f and f.title and V.title_in_block(f, [block]):
            return d
    return ''


def import_files(engine: Engine, paths: list[Path], dois: list[str]) -> list[dict]:
    """Rows describing what happened to each file. Facts come from the registry
    (through the engine's journal cache when present)."""
    rows = []
    facts_by_doi: dict[str, V.Facts] = {}
    for d in dois:
        st, f, _ = engine.registry.facts(d)
        if st == 'ok':
            f.twins = engine.registry.twins(f)
            facts_by_doi[d] = f
    for name, data in gather(paths):
        row = {'File given': name, 'DOI': '', 'Status': '', 'Proof': '', 'Filed as': '', 'Note': ''}
        rows.append(row)
        if not data:
            row.update(Status='Unreadable', Note='could not read this file')
            continue
        doi = match_doi(data, list(facts_by_doi), facts_by_doi)
        if not doi:
            row.update(Status='Not in your list', Note='no DOI or title from your list found in it; left where it is')
            continue
        facts = facts_by_doi[doi]
        row['DOI'] = doi
        v = V.verify(data, facts, published=True)
        res = RoundResult('Import: ' + name, 'imported file', v, hashlib.sha256(data).hexdigest(), data,
                          'publishedVersion' if v.ok and v.self_doi == facts.doi else '', True)
        erow = {c: '' for c in __import__('engine').COLUMNS}
        erow.update(DOI=doi, **{'Title (registry)': facts.title, 'First author': facts.authors[0] if facts.authors else ''})
        engine.journal.setdefault(doi, {}).update(title=facts.title, author=erow['First author'], year=facts.year,
                                                  venue=facts.venue, authors=facts.authors, retracted=facts.retracted)
        if v.ok:
            engine._file(doi, facts, res, erow, 1, 'PDFs')
            row.update(Status='Verified', Proof=v.proof, **{'Filed as': erow['File']}, Note=erow['Note'])
        else:
            erow['Status'] = 'Not checkable: scanned' if v.reason_code == 'no_text' else 'Check these'
            erow['Note'] = 'Import: ' + v.reason
            engine._file(doi, facts, res, erow, 1, 'check')
            row.update(Status=erow['Status'], **{'Filed as': erow['File']}, Note=v.reason)
    return rows
