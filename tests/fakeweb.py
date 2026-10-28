"""A stand-in for the network. Every test runs against this, never the internet."""
import json
import urllib.parse

import sources as S


class FakeWeb:
    """routes: exact URL -> Response, or a list of Responses served in order.
    patterns: substring -> Response, checked when no exact route matches."""

    def __init__(self, routes=None, patterns=None, email='tester@example.org'):
        self.routes = dict(routes or {})
        self.patterns = dict(patterns or {})
        self.email = email
        self.ua = f'DOI-Downloader/{S.VERSION} (mailto:{email})'
        self.log = []
        self.headers_seen = []

    def get(self, url, accept='*/*', headers=None, timeout=60):
        self.log.append(url)
        self.headers_seen.append(dict(headers or {}))
        r = self.routes.get(url)
        if r is None:
            for sub, resp in self.patterns.items():
                if sub in url:
                    r = resp
                    break
        if isinstance(r, list):
            r = r.pop(0) if r else None
        if r is None:
            return S.Response(404, url, reason='Not Found')
        return r


def ok_json(obj, url='https://x'):
    return S.Response(200, url, 'application/json', json.dumps(obj).encode())


def ok_pdf(data, url='https://x'):
    return S.Response(200, url, 'application/pdf', data, content_length=len(data))


def html_page(url='https://x'):
    return S.Response(200, url, 'text/html', b'<html><body>Please sign in</body></html>')


def crossref_record(doi, title, family='Cruz', pages='1230-1236', kind='proceedings-article',
                    retracted=False, subtitle=None, links=None):
    m = {'DOI': doi, 'title': [title], 'type': kind, 'page': pages,
         'author': [{'family': family, 'given': 'J.'}, {'family': 'Lee', 'given': 'B.'}],
         'issued': {'date-parts': [[2024]]}, 'container-title': ['Test Venue']}
    if subtitle:
        m['subtitle'] = [subtitle]
    if retracted:
        m['updated-by'] = [{'type': 'retraction', 'source': 'retraction-watch', 'DOI': '10.9/ret'}]
    if links:
        m['link'] = links
    return {'message': m}


def unpaywall_record(doi, locs):
    return {'doi': doi, 'is_oa': bool(locs), 'oa_locations': locs,
            'best_oa_location': locs[0] if locs else None}


def q(s):
    return urllib.parse.quote(s, safe='/()')
