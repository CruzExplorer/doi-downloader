"""sources.py: where the facts and the files come from.

Every request identifies this program honestly, goes to a service that
permits programs, and keeps that service's pacing. Services that do not
permit programs are never asked (see NEVER_ASK).

Facts (title, authors, pages, type, retraction) come from the DOI
registries: Crossref first, then DataCite; arXiv's own API for arXiv DOIs.
Files come from Unpaywall's list of free copies, Semantic Scholar, arXiv,
Crossref's text-mining links, and (with the user's own key) OpenAlex,
CORE, Wiley, Elsevier and Springer Nature.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from verify import Facts, clean_registry_text, norm_doi, words

VERSION = '2.0'
MAX_BYTES = 150 * 1024 * 1024
MAX_WAIT_429 = 120
RETRY_WAITS = (5, 15, 45)              # transient faults: timeouts, dropped connections, 5xx

# publishers whose rules do not allow download programs: never contacted
NEVER_ASK_HOSTS = ('dl.acm.org', 'acm.org')
NEVER_ASK_PREFIXES = ('10.1145/', '10.5555/')
NEVER_ASK_REASON = 'ACM does not allow download programs; its papers are listed for you to obtain yourself'

GAP = {                                # seconds between requests to the same host
    'arxiv.org': 15.0, 'export.arxiv.org': 3.0,
    'api.crossref.org': 1.0, 'api.datacite.org': 1.0, 'api.unpaywall.org': 1.0,
    'api.semanticscholar.org': 1.1, 'api.openalex.org': 0.2, 'content.openalex.org': 1.0,
    'api.core.ac.uk': 2.0,
}
DEFAULT_GAP = 1.0


# ------------------------------------------------------------------ HTTP

@dataclass
class Response:
    status: int
    url: str
    ctype: str = ''
    data: bytes = b''
    headers: dict = field(default_factory=dict)
    reason: str = ''
    content_length: int | None = None

    @property
    def is_pdf(self) -> bool:
        return b'%PDF-' in self.data[:1024]

    def json(self):
        return json.loads(self.data.decode('utf-8'))


class Web:
    """The only part that touches the network. Tests replace it."""

    def __init__(self, email: str, log=None):
        self.email = email
        self.ua = f'DOI-Downloader/{VERSION} (mailto:{email}; https://github.com)' if email else f'DOI-Downloader/{VERSION}'
        self.log = log if log is not None else []          # every URL requested, in order
        self.opener = urllib.request.build_opener()

    def get(self, url: str, accept: str = '*/*', headers: dict | None = None, timeout: int = 60) -> Response:
        self.log.append(url)
        h = {'User-Agent': self.ua, 'Accept': accept}
        h.update(headers or {})
        req = urllib.request.Request(url, headers=h)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                data = r.read(MAX_BYTES + 1)
                cl = r.headers.get('Content-Length')
                return Response(r.status, r.geturl(), r.headers.get('Content-Type', ''), data,
                                dict(r.headers), '', int(cl) if cl and cl.isdigit() else None)
        except urllib.error.HTTPError as e:
            try:
                body = e.read(4000)
            except Exception:
                body = b''
            return Response(e.code, url, (e.headers or {}).get('Content-Type', ''), body,
                            dict(e.headers or {}), str(e.reason))
        except Exception as e:
            return Response(0, url, reason=f'{type(e).__name__}: {e}')


class Pacer:
    def __init__(self, sleep=time.sleep, clock=time.monotonic):
        self.sleep, self.clock, self.last = sleep, clock, {}

    def wait(self, url: str):
        host = urllib.parse.urlsplit(url).netloc.lower()
        gap = GAP.get(host, DEFAULT_GAP)
        if host in self.last:
            left = gap - (self.clock() - self.last[host])
            if left > 0:
                self.sleep(left)
        self.last[host] = self.clock()


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def never_ask(url: str, doi: str = '') -> bool:
    h = host_of(url)
    if any(h == x or h.endswith('.' + x) for x in NEVER_ASK_HOSTS):
        return True
    d = norm_doi(doi)
    if h in ('doi.org', 'dx.doi.org') and any(d.startswith(p) for p in NEVER_ASK_PREFIXES):
        return True
    if any(d.startswith(p) for p in NEVER_ASK_PREFIXES) and 'doi.org/' + d in url.lower():
        return True
    return False


def retry_after(r: Response) -> int | None:
    try:
        return int(float(r.headers.get('Retry-After', '')))
    except (TypeError, ValueError):
        return None


class Client:
    """GET with pacing, honest identity, and the fault rules of the plan:
    transient faults retried (5, 15, 45 s); 403/404 never; 429 waited for once, up to 2 min;
    a host that answered a file request with a web page is not asked again this run."""

    def __init__(self, web: Web, pacer: Pacer | None = None, sleep=time.sleep):
        self.web, self.pacer, self.sleep = web, pacer or Pacer(sleep), sleep
        self.paused_hosts: dict[str, str] = {}             # host -> reason

    def get(self, url: str, accept='*/*', headers=None, retries=RETRY_WAITS) -> Response:
        host = host_of(url)
        if host in self.paused_hosts:
            return Response(0, url, reason='paused: ' + self.paused_hosts[host])
        for i, wait in enumerate((*retries, None)):
            self.pacer.wait(url)
            r = self.web.get(url, accept, headers)
            if r.status == 429:
                ra = retry_after(r)
                if ra is None:
                    ra = 30
                if ra > MAX_WAIT_429:
                    r.reason = f'service busy (asked to wait {ra} s)'
                    return r
                self.sleep(ra)
                r = self.web.get(url, accept, headers)
                if r.status == 429:
                    r.reason = 'service busy'
                    return r
            transient = r.status == 0 or r.status >= 500
            if transient and wait is not None:
                self.sleep(wait)
                continue
            return r
        return r

    def get_file(self, url: str) -> Response:
        """A file request. A web page in reply means the site is not giving files
        to programs; that host is left alone for the rest of the run."""
        r = self.get(url, accept='application/pdf,*/*;q=0.5')
        if r.status == 200 and not r.is_pdf:
            self.paused_hosts[host_of(url)] = 'this site gave a web page, not a file'
            r.reason = 'web page, not a file'
        return r


# ------------------------------------------------------------------ registries

def _pages(s: str) -> tuple[int | None, int | None]:
    m = re.match(r'\s*(\d+)\s*[-–]\s*(\d+)\s*$', s or '')
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r'\s*(\d+)\s*$', s or '')
    return (int(m.group(1)), int(m.group(1))) if m else (None, None)


def crossref_facts(doi: str, rec: dict) -> Facts:
    m = rec.get('message', rec)
    title = clean_registry_text((m.get('title') or [''])[0])
    sub = clean_registry_text((m.get('subtitle') or [''])[0]) if m.get('subtitle') else ''
    authors = []
    for a in m.get('author') or []:
        fam = a.get('family') or a.get('name') or ''
        if fam:
            authors.append(clean_registry_text(fam))
    year = ''
    for k in ('published', 'published-print', 'published-online', 'issued', 'created'):
        parts = (m.get(k) or {}).get('date-parts') or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])
            break
    first, last = _pages(m.get('page') or '')
    retracted = any((u.get('type') == 'retraction') for u in (m.get('updated-by') or []))
    venue = clean_registry_text((m.get('container-title') or [''])[0]) if m.get('container-title') else ''
    abstract = clean_registry_text(m.get('abstract') or '')
    f = Facts(doi=norm_doi(doi), title=title, subtitle=sub, authors=authors, year=year,
              page_first=first, page_last=last, kind=m.get('type') or '', retracted=retracted,
              venue=venue, abstract=abstract)
    return f


def datacite_facts(doi: str, rec: dict) -> Facts:
    a = (rec.get('data') or {}).get('attributes') or {}
    titles = a.get('titles') or [{}]
    authors = []
    for c in a.get('creators') or []:
        fam = c.get('familyName') or (c.get('name') or '').split(',')[0]
        if fam:
            authors.append(fam)
    year = str(a.get('publicationYear') or '')
    return Facts(doi=norm_doi(doi), title=clean_registry_text(titles[0].get('title', '')),
                 authors=authors, year=year, kind=(a.get('types') or {}).get('resourceTypeGeneral', ''))


def arxiv_id_of(doi: str) -> str:
    m = re.match(r'10\.48550/arxiv\.(.+)$', norm_doi(doi))
    return m.group(1) if m else ''


ATOM = {'a': 'http://www.w3.org/2005/Atom'}


def arxiv_entries(xml: bytes) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for e in root.findall('a:entry', ATOM):
        idurl = (e.findtext('a:id', '', ATOM) or '').strip()
        m = re.search(r'arxiv\.org/abs/(.+)$', idurl)
        out.append({
            'id': m.group(1) if m else idurl,
            'title': re.sub(r'\s+', ' ', e.findtext('a:title', '', ATOM) or '').strip(),
            'authors': [(n.findtext('a:name', '', ATOM) or '').strip().split()[-1]
                        for n in e.findall('a:author', ATOM) if (n.findtext('a:name', '', ATOM) or '').strip()],
            'year': (e.findtext('a:published', '', ATOM) or '')[:4],
        })
    return out


class Registry:
    CROSSREF = 'https://api.crossref.org/works/'
    DATACITE = 'https://api.datacite.org/dois/'
    ARXIV_API = 'https://export.arxiv.org/api/query?'

    def __init__(self, client: Client, email: str):
        self.c, self.email = client, email

    def _mailto(self):
        return '?mailto=' + urllib.parse.quote(self.email) if self.email else ''

    def facts(self, doi: str) -> tuple[str, Facts | None, str]:
        """('ok', facts, '') | ('not_registered', None, note) | ('unreachable', None, note)"""
        doi = norm_doi(doi)
        if arxiv_id_of(doi):
            aid = arxiv_id_of(doi)
            r = self.c.get(self.ARXIV_API + urllib.parse.urlencode({'id_list': aid}))
            if r.status == 200:
                for e in arxiv_entries(r.data):
                    return 'ok', Facts(doi=doi, title=e['title'], authors=e['authors'], year=e['year'],
                                       kind='posted-content', arxiv_id=aid), ''
            if r.status == 0 or r.status >= 500 or r.status == 429:
                return 'unreachable', None, f'arXiv: {r.reason or r.status}'
        r = self.c.get(self.CROSSREF + urllib.parse.quote(doi, safe='/()') + self._mailto(),
                       accept='application/json')
        if r.status == 200:
            try:
                return 'ok', crossref_facts(doi, r.json()), ''
            except ValueError:
                return 'unreachable', None, 'Crossref sent something that is not JSON'
        if r.status not in (404,):
            return 'unreachable', None, f'Crossref: {r.reason or r.status}'
        r2 = self.c.get(self.DATACITE + urllib.parse.quote(doi, safe='/()'), accept='application/json')
        if r2.status == 200:
            try:
                return 'ok', datacite_facts(doi, r2.json()), ''
            except ValueError:
                return 'unreachable', None, 'DataCite sent something that is not JSON'
        if r2.status == 404:
            return 'not_registered', None, 'neither Crossref nor DataCite knows this DOI'
        return 'unreachable', None, f'DataCite: {r2.reason or r2.status}'

    def twins(self, facts: Facts) -> list[str]:
        """Other DOIs whose registry title and first author are identical."""
        if not facts.title or not facts.authors:
            return []
        q = urllib.parse.urlencode({'query.bibliographic': facts.title, 'rows': 10, 'select': 'DOI,title,author'})
        url = self.CROSSREF[:-1] + '?' + q + ('&mailto=' + urllib.parse.quote(self.email) if self.email else '')
        r = self.c.get(url, accept='application/json')
        if r.status != 200:
            return []
        try:
            items = r.json()['message']['items']
        except (ValueError, KeyError):
            return []
        mine, fam = words(facts.title), words(facts.authors[0])
        out = []
        for it in items:
            d = norm_doi(it.get('DOI', ''))
            if not d or d == facts.doi:
                continue
            t = words(clean_registry_text((it.get('title') or [''])[0]))
            a = it.get('author') or []
            f2 = words(a[0].get('family', '')) if a else []
            if t == mine and fam and f2 == fam:
                out.append(d)
        return out

    def crossref_links(self, doi: str) -> list[dict]:
        """Crossref 'link' entries intended for text mining (no token needed)."""
        r = self.c.get(self.CROSSREF + urllib.parse.quote(norm_doi(doi), safe='/()') + self._mailto(),
                       accept='application/json')
        if r.status != 200:
            return []
        try:
            links = r.json()['message'].get('link') or []
        except (ValueError, KeyError):
            return []
        return [{'url': l['URL'], 'label': 'Crossref text-mining link', 'version': 'publishedVersion', 'host': 'publisher'}
                for l in links if l.get('intended-application') == 'text-mining'
                and 'pdf' in (l.get('content-type') or '').lower()]


# ------------------------------------------------------------------ candidates

@dataclass
class Candidate:
    url: str
    label: str                 # where it came from
    version: str = ''          # publishedVersion / acceptedVersion / submittedVersion / preprint
    host: str = ''             # publisher / repository / arxiv / openalex ...
    headers: dict = field(default_factory=dict)

    @property
    def published(self) -> bool:
        return self.version == 'publishedVersion' and self.host == 'publisher'

    def key(self) -> str:
        """Same copy under two spellings counts once."""
        u = self.url.strip()
        m = re.search(r'arxiv\.org/(?:abs|pdf)/([0-9.]+|[a-z-]+/\d+)(?:v\d+)?', u, re.I)
        if m:
            return 'arxiv:' + m.group(1).lower()
        u = re.sub(r'^https?://', '', u)
        return u.rstrip('/').lower()


def arxiv_pdf_url(s: str) -> str:
    m = re.search(r'arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?/?$', s or '', re.I)
    return f'https://arxiv.org/pdf/{m.group(1)}' if m else ''


class Finder:
    UNPAYWALL = 'https://api.unpaywall.org/v2/'
    S2 = 'https://api.semanticscholar.org/graph/v1/paper/'
    OPENALEX = 'https://api.openalex.org/works/'
    CORE = 'https://api.core.ac.uk/v3/'
    WILEY = 'https://api.wiley.com/onlinelibrary/tdm/v1/articles/'
    ELSEVIER = 'https://api.elsevier.com/content/article/doi/'
    SPRINGER = 'https://api.springernature.com/openaccess/jats'

    def __init__(self, client: Client, registry: Registry, email: str, keys: dict | None = None,
                 extra_resolvers: list[str] | None = None):
        self.c, self.reg, self.email = client, registry, email
        self.keys = {k: v for k, v in (keys or {}).items() if v}
        self.extra = extra_resolvers or []
        self.openalex_used_today = 0

    def candidates(self, facts: Facts) -> tuple[list[Candidate], list[str]]:
        """Free copies in the order to try, and the notes for 'sources tried'."""
        doi = facts.doi
        cands, notes = [], []
        if any(doi.startswith(p) for p in NEVER_ASK_PREFIXES):
            notes.append(NEVER_ASK_REASON)
        cands += self._unpaywall(doi, notes)
        if 'openalex' in self.keys:
            cands += self._openalex(doi, notes)
        cands += self._semantic_scholar(doi, facts, notes)
        if facts.arxiv_id:
            cands.append(Candidate(f'https://arxiv.org/pdf/{facts.arxiv_id}', 'arXiv (DOI)', 'submittedVersion', 'arxiv'))
        if not arxiv_id_of(doi):                       # Crossref holds no record for an arXiv DOI
            cands += [Candidate(**l) for l in self.reg.crossref_links(doi)]
        if 'core' in self.keys:
            cands += self._core(doi, notes)
        for pub, key in (('wiley', '10.1002/'), ('wiley', '10.1111/'), ('elsevier', '10.1016/'),
                         ('springer', '10.1007/'), ('springer', '10.1186/'), ('springer', '10.1038/')):
            if pub in self.keys and doi.startswith(key):
                cands += self._publisher_api(pub, doi)
        for tmpl in self.extra:
            cands.append(Candidate(tmpl.replace('{doi}', doi), 'your own resolver', '', 'resolver'))
        # arXiv by identical title, only if nothing else was found
        if not cands and facts.title:
            cands += self._arxiv_title(facts, notes)
        return self._dedupe(cands, doi, notes), notes

    def _dedupe(self, cands, doi, notes):
        seen, out = set(), []
        for c in cands:
            if never_ask(c.url, doi):
                if NEVER_ASK_REASON not in notes:
                    notes.append(NEVER_ASK_REASON)
                continue
            if not c.url.lower().startswith('http'):
                continue
            k = c.key()
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
        # publisher copies first, then accepted, then preprints
        rank = {'publishedVersion': 0, 'acceptedVersion': 1, 'submittedVersion': 2, '': 3}
        out.sort(key=lambda c: (rank.get(c.version, 3), 0 if c.host == 'publisher' else 1))
        return out

    def _unpaywall(self, doi, notes):
        if not self.email:
            notes.append('Unpaywall needs an email address (Setup)')
            return []
        r = self.c.get(self.UNPAYWALL + urllib.parse.quote(doi, safe='/()') + '?email=' + urllib.parse.quote(self.email),
                       accept='application/json')
        if r.status == 404:
            notes.append('Unpaywall: DOI unknown')
            return []
        if r.status != 200:
            notes.append(f'Unpaywall: {r.reason or r.status}')
            return []
        try:
            rec = r.json()
        except ValueError:
            notes.append('Unpaywall: bad reply')
            return []
        out = []
        locs = list(rec.get('oa_locations') or [])
        best = rec.get('best_oa_location')
        if best and best not in locs:
            locs.insert(0, best)
        for loc in locs:
            u = loc.get('url_for_pdf') or ''
            ax = arxiv_pdf_url(u or loc.get('url') or '')
            if ax:
                out.append(Candidate(ax, 'Unpaywall (arXiv)', 'submittedVersion', 'arxiv'))
            elif u:
                out.append(Candidate(u, f'Unpaywall ({loc.get("host_type") or "copy"})',
                                     loc.get('version') or '', loc.get('host_type') or ''))
        if not out and rec.get('is_oa') is False:
            notes.append('Unpaywall: no free copy known')
        return out

    def _semantic_scholar(self, doi, facts, notes):
        r = self.c.get(self.S2 + 'DOI:' + urllib.parse.quote(doi, safe='/()') + '?fields=openAccessPdf,externalIds',
                       accept='application/json')
        if r.status != 200:
            if r.status not in (404,):
                notes.append(f'Semantic Scholar: {r.reason or r.status}')
            return []
        try:
            d = r.json()
        except ValueError:
            return []
        out = []
        ax = (d.get('externalIds') or {}).get('ArXiv')
        if ax:
            facts.arxiv_id = facts.arxiv_id or ax
            out.append(Candidate(f'https://arxiv.org/pdf/{ax}', 'Semantic Scholar (arXiv ID)', 'submittedVersion', 'arxiv'))
        u = (d.get('openAccessPdf') or {}).get('url')
        if u:
            out.append(Candidate(arxiv_pdf_url(u) or u, 'Semantic Scholar', '', 'repository'))
        return out

    def _arxiv_title(self, facts, notes):
        q = 'ti:"' + re.sub(r'[^\w\s-]', ' ', facts.title).strip() + '"'
        r = self.c.get(Registry.ARXIV_API + urllib.parse.urlencode({'search_query': q, 'max_results': 5}))
        if r.status != 200:
            return []
        mine = words(facts.title)
        for e in arxiv_entries(r.data):
            if words(e['title']) == mine:
                notes.append(f'arXiv title match: {e["id"]}')
                return [Candidate(f'https://arxiv.org/pdf/{e["id"]}', 'arXiv (identical title)', 'submittedVersion', 'arxiv')]
        return []

    def _openalex(self, doi, notes):
        if self.openalex_used_today >= 90:
            notes.append('OpenAlex: today\'s free allowance kept in reserve (90 used)')
            return []
        key = self.keys['openalex']
        r = self.c.get(self.OPENALEX + 'doi:' + urllib.parse.quote(doi, safe='/()') + '?select=id,has_content',
                       accept='application/json', headers={'Authorization': 'Bearer ' + key})
        if r.status != 200:
            notes.append(f'OpenAlex: {r.reason or r.status}')
            return []
        try:
            d = r.json()
        except ValueError:
            return []
        if (d.get('has_content') or {}).get('pdf'):
            wid = (d.get('id') or '').rsplit('/', 1)[-1]
            return [Candidate(f'https://content.openalex.org/works/{wid}.pdf', 'OpenAlex copy', 'publishedVersion',
                              'openalex', {'Authorization': 'Bearer ' + key})]
        return []

    def _core(self, doi, notes):
        q = urllib.parse.urlencode({'q': f'doi:"{doi}"', 'limit': 3})
        r = self.c.get(self.CORE + 'search/works?' + q, accept='application/json',
                       headers={'Authorization': 'Bearer ' + self.keys['core']})
        if r.status != 200:
            notes.append(f'CORE: {r.reason or r.status}')
            return []
        try:
            res = r.json().get('results') or []
        except ValueError:
            return []
        out = []
        for w in res:
            u = w.get('downloadUrl')
            if u and norm_doi(w.get('doi') or '') == doi:
                out.append(Candidate(u, 'CORE (repository copy)', 'acceptedVersion', 'repository',
                                     {'Authorization': 'Bearer ' + self.keys['core']}))
        return out

    def _publisher_api(self, pub, doi):
        q = urllib.parse.quote(doi, safe='/()')
        if pub == 'wiley':
            return [Candidate(self.WILEY + q, 'Wiley TDM service', 'publishedVersion', 'publisher',
                              {'Wiley-TDM-Client-Token': self.keys['wiley']})]
        if pub == 'elsevier':
            return [Candidate(self.ELSEVIER + q + '?httpAccept=application/pdf', 'Elsevier article service',
                              'publishedVersion', 'publisher', {'X-ELS-APIKey': self.keys['elsevier']})]
        if pub == 'springer':
            return [Candidate(self.SPRINGER + '?' + urllib.parse.urlencode({'q': f'doi:{doi}', 'api_key': self.keys['springer']}),
                              'Springer Nature open-access service', 'publishedVersion', 'publisher')]
        return []

    def fetch(self, cand: Candidate) -> Response:
        if cand.host == 'openalex':
            self.openalex_used_today += 1
        return self.c.get_file(cand.url) if not cand.headers else self._get_file_with(cand)

    def _get_file_with(self, cand: Candidate) -> Response:
        r = self.c.get(cand.url, accept='application/pdf,*/*;q=0.5', headers=cand.headers)
        if r.status == 200 and not r.is_pdf:
            r.reason = 'web page, not a file'
        return r


# ------------------------------------------------------------------ in-app screens (metadata only)

class Lookup:
    """Paper page, snowballing and search: metadata from services that permit programs."""

    def __init__(self, client: Client, email: str, keys: dict | None = None):
        self.c, self.email, self.keys = client, email, {k: v for k, v in (keys or {}).items() if v}

    def references(self, doi: str) -> list[dict]:
        return self._s2_list(doi, 'references', 'citedPaper')

    def citations(self, doi: str) -> list[dict]:
        return self._s2_list(doi, 'citations', 'citingPaper')

    def _s2_list(self, doi, kind, key):
        url = Finder.S2 + 'DOI:' + urllib.parse.quote(norm_doi(doi), safe='/()') + f'/{kind}?fields=title,year,externalIds,authors&limit=200'
        r = self.c.get(url, accept='application/json')
        if r.status != 200:
            return []
        try:
            items = r.json().get('data') or []
        except ValueError:
            return []
        out = []
        for it in items:
            p = it.get(key) or {}
            d = (p.get('externalIds') or {}).get('DOI') or ''
            out.append({'doi': norm_doi(d), 'title': p.get('title') or '', 'year': p.get('year') or '',
                        'authors': ', '.join(a.get('name', '') for a in (p.get('authors') or [])[:3])})
        return out

    def search(self, query: str, limit: int = 25) -> list[dict]:
        url = Finder.S2 + 'search?' + urllib.parse.urlencode({'query': query, 'limit': limit, 'fields': 'title,year,externalIds,authors'})
        r = self.c.get(url, accept='application/json')
        if r.status == 200:
            try:
                return [{'doi': norm_doi((p.get('externalIds') or {}).get('DOI') or ''), 'title': p.get('title') or '',
                         'year': p.get('year') or '', 'authors': ', '.join(a.get('name', '') for a in (p.get('authors') or [])[:3])}
                        for p in r.json().get('data') or []]
            except ValueError:
                pass
        q = urllib.parse.urlencode({'query': query, 'rows': limit, 'select': 'DOI,title,author,issued'})
        r = self.c.get(Registry.CROSSREF[:-1] + '?' + q + ('&mailto=' + urllib.parse.quote(self.email) if self.email else ''),
                       accept='application/json')
        if r.status != 200:
            return []
        try:
            items = r.json()['message']['items']
        except (ValueError, KeyError):
            return []
        out = []
        for it in items:
            yr = ((it.get('issued') or {}).get('date-parts') or [[None]])[0][0]
            out.append({'doi': norm_doi(it.get('DOI', '')), 'title': clean_registry_text((it.get('title') or [''])[0]),
                        'year': yr or '', 'authors': ', '.join(f"{a.get('given', '')} {a.get('family', '')}".strip()
                                                              for a in (it.get('author') or [])[:3])})
        return out
