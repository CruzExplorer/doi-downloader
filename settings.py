"""settings.py: per-user settings, stored OUTSIDE the program folder.

%APPDATA%\\DOI Downloader\\settings.json holds the email, the optional keys,
the file-name template and extra resolvers. It is never inside the program
folder, so it cannot end up in a repository. Environment variables override
the file: DOI_DL_EMAIL, DOI_DL_OPENALEX_KEY, DOI_DL_CORE_KEY,
DOI_DL_WILEY_KEY, DOI_DL_ELSEVIER_KEY, DOI_DL_SPRINGER_KEY.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

KEY_NAMES = ('openalex', 'core', 'wiley', 'elsevier', 'springer')
DEFAULT_TEMPLATE = '{author}_{year}_{title}'

# Short, factual guide shown in Setup and copied into README.md.
GUIDE = {
    'email': ('Your email (required)',
              'Used by Unpaywall (free copies) and Crossref (title, authors, pages). Free, no account.',
              'https://unpaywall.org/products/api'),
    'openalex': ('OpenAlex key (optional, free)',
                 'OpenAlex\'s own PDF copies of open-access papers. Free key: about 100 PDFs a day; '
                 'this program stops at 90. Sign up at openalex.org, then Settings > API key.',
                 'https://openalex.org'),
    'core': ('CORE key (optional, free)',
             'Author copies in university repositories. Register at core.ac.uk/services/api.',
             'https://core.ac.uk/services/api'),
    'wiley': ('Wiley TDM token (optional, free)',
              'Wiley\'s text-and-data-mining service for DOIs starting 10.1002 and 10.1111. '
              'Sign in with your ORCID on Wiley\'s TDM page and accept its licence.',
              'https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining'),
    'elsevier': ('Elsevier API key (optional, free)',
                 'ScienceDirect article service for DOIs starting 10.1016. Personal key from the Elsevier Developer Portal.',
                 'https://dev.elsevier.com'),
    'springer': ('Springer Nature key (optional, free)',
                 'Springer Nature open-access service for DOIs starting 10.1007, 10.1186, 10.1038.',
                 'https://dev.springernature.com'),
}


def settings_dir() -> Path:
    base = os.environ.get('APPDATA') or str(Path.home())
    return Path(base) / 'DOI Downloader'


def settings_path() -> Path:
    return settings_dir() / 'settings.json'


def load() -> dict:
    data = {}
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    data.setdefault('email', '')
    data.setdefault('keys', {})
    data.setdefault('template', DEFAULT_TEMPLATE)
    data.setdefault('resolvers', [])
    env_email = os.environ.get('DOI_DL_EMAIL')
    if env_email:
        data['email'] = env_email
    for k in KEY_NAMES:
        v = os.environ.get(f'DOI_DL_{k.upper()}_KEY')
        if v:
            data['keys'][k] = v
    return data


def save(data: dict) -> Path:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return p


def masked(value: str) -> str:
    v = value or ''
    return (v[:3] + '…' + v[-2:]) if len(v) > 6 else ('•' * len(v))
