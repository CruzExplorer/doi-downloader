"""Run before uploading this folder anywhere. Exits 1 if the folder holds anything
personal: an email address, something shaped like a key or token, a settings file,
a downloaded PDF, a report, a journal or a manifest. Exits 0 when clean.

    C:\\SLSU-Python\\Scripts\\python.exe check_before_publish.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
TOKEN = re.compile(r'(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[\'"]?[A-Za-z0-9_\-]{12,}')
LONG_HEX = re.compile(r'\b[0-9a-f]{32,}\b')
ALLOWED_EMAILS = {'dl-info@hq.acm.org', 'permissions@acm.org', 'tester@example.org'}
FORBIDDEN_FILES = ('settings', 'download_log.json', 'manifest.csv', 'references.ris', 'references.bib')
TEXT_EXT = {'.py', '.pyw', '.md', '.txt', '.json', '.cfg', '.ini', '.csv', '.toml', '.yaml', '.yml', '.ris', '.bib'}
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', '_v1_backup'}


def problems(root: Path = HERE) -> list[str]:
    out = []
    for p in root.rglob('*'):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_dir():
            continue
        rel = str(p.relative_to(root))
        low = p.name.lower()
        if low.startswith('settings') and p.suffix in ('.json', '.txt', '.ini'):
            out.append(f'{rel}: settings file')
        if low in FORBIDDEN_FILES or low.startswith('report') and p.suffix == '.csv':
            out.append(f'{rel}: run output')
        if p.suffix.lower() == '.pdf' and 'tests' not in p.parts:
            out.append(f'{rel}: a PDF')
        if p.suffix.lower() in TEXT_EXT:
            try:
                text = p.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for m in EMAIL.findall(text):
                if m.lower() not in ALLOWED_EMAILS and not m.lower().endswith('@example.org'):
                    out.append(f'{rel}: email address {m}')
            if p.name != Path(__file__).name:
                if TOKEN.search(text):
                    out.append(f'{rel}: something shaped like a key or token')
                if LONG_HEX.search(text) and 'tests' not in p.parts:
                    out.append(f'{rel}: a long hex string (a key or hash?)')
    return out


def main(argv=None) -> int:
    root = Path(argv[1]) if argv and len(argv) > 1 else HERE
    found = problems(root)
    if found:
        print('NOT READY TO PUBLISH:')
        for f in found:
            print('  -', f)
        return 1
    print('Clean: no personal data, keys, settings or outputs in', root)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
