"""sources.py against a fake network: pacing, identity, faults, never-ask, candidates."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import sources as S                                                  # noqa: E402
from fakeweb import FakeWeb, ok_json, ok_pdf, html_page, crossref_record, unpaywall_record, q   # noqa: E402
from pdfmake import make_pdf                                         # noqa: E402

EMAIL = 'tester@example.org'
DOI = '10.3390/su16031245'


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def now(self):
        return self.t


def client(web, clock=None):
    clock = clock or Clock()
    return S.Client(web, S.Pacer(clock.sleep, clock.now), clock.sleep), clock


def test_identity_header_on_every_request():
    web = S.Web(EMAIL)
    assert web.ua.startswith('DOI-Downloader/') and 'mailto:tester@example.org' in web.ua


def test_never_ask_acm():
    assert S.never_ask('https://dl.acm.org/doi/pdf/10.1145/1.2', '10.1145/1.2')
    assert S.never_ask('https://doi.org/10.1145/1.2', '10.1145/1.2')
    assert not S.never_ask('https://arxiv.org/pdf/2301.00001', '10.1145/1.2')
    assert not S.never_ask('https://doi.org/10.3390/x', '10.3390/x')


def test_acm_candidates_are_dropped_before_any_request():
    doi = '10.1145/3626252.3630874'
    web = FakeWeb({
        S.Finder.UNPAYWALL + q(doi) + '?email=tester%40example.org': ok_json(unpaywall_record(doi, [
            {'url_for_pdf': 'https://dl.acm.org/doi/pdf/' + doi, 'version': 'publishedVersion', 'host_type': 'publisher'},
            {'url_for_pdf': 'https://arxiv.org/pdf/2301.00001', 'version': 'submittedVersion', 'host_type': 'repository'}])),
    })
    c, _ = client(web)
    f = S.Finder(c, S.Registry(c, EMAIL), EMAIL)
    facts = S.Facts(doi=doi, title='T', authors=['Cruz'])
    cands, notes = f.candidates(facts)
    assert [x.url for x in cands] == ['https://arxiv.org/pdf/2301.00001'], cands
    assert S.NEVER_ASK_REASON in notes
    assert not any('acm.org' in u for u in web.log)


def test_pacing_arxiv_15s_and_api_1s():
    web = FakeWeb({'https://arxiv.org/pdf/a': ok_pdf(make_pdf()), 'https://arxiv.org/pdf/b': ok_pdf(make_pdf())})
    c, clock = client(web)
    c.get_file('https://arxiv.org/pdf/a')
    c.get_file('https://arxiv.org/pdf/b')
    assert abs(sum(clock.slept) - 15.0) < 1e-9, clock.slept


def test_web_page_instead_of_file_pauses_host():
    web = FakeWeb({'https://pub.example/a.pdf': html_page(), 'https://pub.example/b.pdf': ok_pdf(make_pdf())})
    c, _ = client(web)
    r1 = c.get_file('https://pub.example/a.pdf')
    r2 = c.get_file('https://pub.example/b.pdf')
    assert r1.reason == 'web page, not a file'
    assert r2.status == 0 and r2.reason.startswith('paused'), r2
    assert web.log == ['https://pub.example/a.pdf']                  # the second was never sent


def test_403_not_retried_and_5xx_retried():
    web = FakeWeb({'https://a/x': S.Response(403, 'https://a/x'),
                   'https://b/x': [S.Response(503, 'https://b/x'), S.Response(503, 'https://b/x'), ok_pdf(make_pdf())]})
    c, clock = client(web)
    assert c.get('https://a/x').status == 403 and web.log.count('https://a/x') == 1
    assert c.get('https://b/x').status == 200 and web.log.count('https://b/x') == 3
    assert 5 in clock.slept and 15 in clock.slept


def test_429_waits_once_then_service_busy():
    busy = lambda: S.Response(429, 'https://s/x', headers={'Retry-After': '7'})
    web = FakeWeb({'https://s/x': [busy(), busy()], 'https://s/y': [busy(), ok_json({'a': 1})],
                   'https://s/z': [S.Response(429, 'https://s/z', headers={'Retry-After': '9999'})]})
    c, clock = client(web)
    assert c.get('https://s/x').reason == 'service busy'
    assert c.get('https://s/y').status == 200 and 7 in clock.slept
    r = c.get('https://s/z')
    assert r.status == 429 and 'service busy' in r.reason and 9999 not in clock.slept


def test_registry_crossref_then_datacite_then_not_registered():
    cr = crossref_record(DOI, 'A <i>Title</i> &amp; More', pages='1245-1245')
    web = FakeWeb({S.Registry.CROSSREF + q(DOI) + '?mailto=tester%40example.org': ok_json(cr),
                   S.Registry.DATACITE + q('10.5281/zenodo.1'): ok_json({'data': {'attributes': {
                       'titles': [{'title': 'Zen'}], 'creators': [{'familyName': 'Ana'}], 'publicationYear': 2023}}})})
    c, _ = client(web)
    reg = S.Registry(c, EMAIL)
    st, f, _ = reg.facts(DOI)
    assert st == 'ok' and f.title == 'A Title & More' and f.authors == ['Cruz', 'Lee'] and f.page_range is None
    st2, f2, _ = reg.facts('10.5281/zenodo.1')
    assert st2 == 'ok' and f2.title == 'Zen' and f2.authors == ['Ana']
    st3, _, note = reg.facts('10.9999/nothing')
    assert st3 == 'not_registered', note


def test_registry_unreachable_is_not_wrong_paper():
    web = FakeWeb({S.Registry.CROSSREF + q(DOI) + '?mailto=tester%40example.org': [S.Response(0, 'x', reason='timed out')] * 4})
    c, _ = client(web)
    st, f, note = S.Registry(c, EMAIL).facts(DOI)
    assert st == 'unreachable' and f is None and 'timed out' in note


def test_retraction_flag_from_crossref():
    cr = crossref_record(DOI, 'Retracted thing here', retracted=True)
    f = S.crossref_facts(DOI, cr)
    assert f.retracted


def test_twins_identical_title_and_author():
    items = [{'DOI': '10.1/other', 'title': ['Same Title Here'], 'author': [{'family': 'Cruz'}]},
             {'DOI': '10.1/diff', 'title': ['Same Title Here'], 'author': [{'family': 'Other'}]},
             {'DOI': DOI, 'title': ['Same Title Here'], 'author': [{'family': 'Cruz'}]}]
    web = FakeWeb(patterns={'query.bibliographic': ok_json({'message': {'items': items}})})
    c, _ = client(web)
    tw = S.Registry(c, EMAIL).twins(S.Facts(doi=DOI, title='Same Title Here', authors=['Cruz']))
    assert tw == ['10.1/other'], tw


def test_candidates_order_and_dedupe():
    web = FakeWeb({
        S.Finder.UNPAYWALL + q(DOI) + '?email=tester%40example.org': ok_json(unpaywall_record(DOI, [
            {'url_for_pdf': 'https://arxiv.org/abs/2301.00001v2', 'version': 'submittedVersion', 'host_type': 'repository'},
            {'url_for_pdf': 'https://www.mdpi.com/x/pdf', 'version': 'publishedVersion', 'host_type': 'publisher'},
            {'url_for_pdf': 'http://www.mdpi.com/x/pdf/', 'version': 'publishedVersion', 'host_type': 'publisher'}])),
        S.Finder.S2 + 'DOI:' + q(DOI) + '?fields=openAccessPdf,externalIds': ok_json(
            {'externalIds': {'ArXiv': '2301.00001'}, 'openAccessPdf': {'url': 'https://arxiv.org/pdf/2301.00001v1'}}),
    })
    c, _ = client(web)
    f = S.Finder(c, S.Registry(c, EMAIL), EMAIL)
    cands, notes = f.candidates(S.Facts(doi=DOI, title='T', authors=['Cruz']))
    keys = [x.key() for x in cands]
    assert keys == ['www.mdpi.com/x/pdf', 'arxiv:2301.00001'], keys       # publisher first, arXiv once


def test_no_keys_means_no_key_services_asked():
    web = FakeWeb({S.Finder.UNPAYWALL + q(DOI) + '?email=tester%40example.org': ok_json(unpaywall_record(DOI, []))})
    c, _ = client(web)
    f = S.Finder(c, S.Registry(c, EMAIL), EMAIL, keys={})
    f.candidates(S.Facts(doi=DOI, title='T', authors=['Cruz']))
    assert not any('openalex' in u or 'core.ac.uk' in u or 'wiley' in u for u in web.log)


def test_each_key_goes_only_to_its_own_host():
    keys = {'openalex': 'OA-KEY', 'core': 'CORE-KEY', 'wiley': 'WILEY-KEY'}
    web = FakeWeb(patterns={'api.openalex.org': ok_json({'id': 'https://openalex.org/W1', 'has_content': {'pdf': True}}),
                            'api.core.ac.uk': ok_json({'results': []}),
                            'unpaywall': ok_json(unpaywall_record('10.1111/jcal.1', []))})
    c, _ = client(web)
    f = S.Finder(c, S.Registry(c, EMAIL), EMAIL, keys=keys)
    cands, _ = f.candidates(S.Facts(doi='10.1111/jcal.1', title='T', authors=['Cruz']))
    for url, h in zip(web.log, web.headers_seen):
        joined = ' '.join(h.values())
        if 'OA-KEY' in joined:
            assert 'openalex.org' in url, url
        if 'CORE-KEY' in joined:
            assert 'core.ac.uk' in url, url
        assert 'WILEY-KEY' not in joined or 'wiley.com' in url
    wiley = [x for x in cands if 'wiley.com' in x.url]
    assert wiley and wiley[0].headers.get('Wiley-TDM-Client-Token') == 'WILEY-KEY'


def test_openalex_daily_cap():
    web = FakeWeb(patterns={'api.openalex.org': ok_json({'id': 'https://openalex.org/W1', 'has_content': {'pdf': True}}),
                            'unpaywall': ok_json(unpaywall_record(DOI, []))})
    c, _ = client(web)
    f = S.Finder(c, S.Registry(c, EMAIL), EMAIL, keys={'openalex': 'k'})
    f.openalex_used_today = 90
    cands, notes = f.candidates(S.Facts(doi=DOI, title='T', authors=['Cruz']))
    assert not any('openalex' in x.url for x in cands) and any('allowance' in n for n in notes)


# ---- mutants
@pytest.mark.parametrize('mutate,test', [
    (lambda: setattr(S, 'NEVER_ASK_HOSTS', ()) or setattr(S, 'NEVER_ASK_PREFIXES', ()), test_acm_candidates_are_dropped_before_any_request),
    (lambda: S.GAP.update({'arxiv.org': 0.0}), test_pacing_arxiv_15s_and_api_1s),
    (lambda: setattr(S.Client, 'get_file', lambda self, url: self.get(url)), test_web_page_instead_of_file_pauses_host),
    (lambda: setattr(S, 'MAX_WAIT_429', 10 ** 9), test_429_waits_once_then_service_busy),
])
def test_mutant_is_caught(mutate, test):
    import importlib
    importlib.reload(S)
    mutate()
    try:
        with pytest.raises(AssertionError):
            test()
    finally:
        importlib.reload(S)
