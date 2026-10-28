"""engine.py end to end on the fake network. No internet, nothing of the user's touched."""
import csv
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import engine as E                                                   # noqa: E402
import sources as S                                                  # noqa: E402
from fakeweb import FakeWeb, ok_json, ok_pdf, html_page, crossref_record, unpaywall_record, q   # noqa: E402
from pdfmake import make_pdf, scanned_pdf                            # noqa: E402

EMAIL = 'tester@example.org'
DOI = '10.3390/su16031245'
TITLE = 'Generative AI Scaffolding for Novice Programmers in Introductory Courses'
PDF_URL = 'https://pub.example/paper.pdf'


def cr_url(doi):
    return S.Registry.CROSSREF + q(doi) + '?mailto=tester%40example.org'


def up_url(doi):
    return S.Finder.UNPAYWALL + q(doi) + '?email=tester%40example.org'


def good_pdf(doi=DOI, title=TITLE, pages=7):
    return make_pdf(title=title, authors='Jane Cruz, Bob Lee', footer=f'https://doi.org/{doi}', pages=pages)


def web_for(doi=DOI, pdf=None, locs=None, title=TITLE, extra=None, cr=None):
    locs = locs if locs is not None else [{'url_for_pdf': PDF_URL, 'version': 'publishedVersion', 'host_type': 'publisher'}]
    routes = {cr_url(doi): ok_json(cr or crossref_record(doi, title, pages='1-7')),
              up_url(doi): ok_json(unpaywall_record(doi, locs)),
              PDF_URL: pdf if pdf is not None else ok_pdf(good_pdf(doi, title), PDF_URL)}
    routes.update(extra or {})
    return FakeWeb(routes, patterns={'query.bibliographic': ok_json({'message': {'items': []}})})


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def now(self):
        return self.t


def engine(tmp, web, **kw):
    clock = Clock()
    e = E.Engine(tmp, EMAIL, web=web, pacer=S.Pacer(clock.sleep, clock.now), sleep=clock.sleep, **kw)
    return e, clock


def run(tmp, web, text=DOI, **kw):
    e, clock = engine(tmp, web, **kw)
    items = E.parse_input(text)
    rows = e.run(items)
    return e, rows, items, clock


def test_happy_path_pass_a_filed_journal_manifest(tmp_path):
    e, rows, items, _ = run(tmp_path, web_for())
    r = rows[0]
    assert r['Status'] == 'Verified' and r['Proof'] == 'PASS-A' and r['Round 1'].startswith('PASS-A'), r
    assert (tmp_path / 'PDFs' / r['File']).is_file() and r['File'] == 'Cruz_2024_Generative-AI-Scaffolding-for-Novice-Programmers-in-Introductory.pdf'
    assert e.journal[DOI]['status'] == 'Verified'
    with open(tmp_path / 'Manifest.csv', encoding='utf-8-sig') as f:
        man = list(csv.DictReader(f))
    assert man[0]['DOI'] == DOI and man[0]['Proof'] == 'PASS-A' and len(man[0]['sha256']) == 64
    assert E.totals_agree(items, rows)


def test_damaged_then_fresh_copy_passes_in_round_2(tmp_path):
    good = good_pdf()
    web = web_for(pdf=[ok_pdf(good[: int(len(good) * 0.8)], PDF_URL), ok_pdf(good, PDF_URL)])
    _, rows, _, clock = run(tmp_path, web)
    r = rows[0]
    assert r['Status'] == 'Verified' and r['Round 1'].startswith('fail (damaged') and r['Round 2'].startswith('PASS-A'), r
    assert 15 in clock.slept, clock.slept                      # the plan's 15 s before a fresh copy


def test_three_wrong_copies_go_to_check_these_with_three_reasons(tmp_path):
    wrong = make_pdf(title='A Totally Different Paper About Databases', authors='Ann Smith',
                     footer='https://doi.org/10.1145/9.9', pages=7)
    locs = [{'url_for_pdf': f'https://pub.example/{i}.pdf', 'version': 'acceptedVersion', 'host_type': 'repository'} for i in range(3)]
    extra = {f'https://pub.example/{i}.pdf': ok_pdf(make_pdf(title=f'Different Paper Number {i} About Other Things',
                                                               authors='Ann Smith', footer=f'doi:10.1145/9.{i}', pages=7),
                                                      f'https://pub.example/{i}.pdf') for i in range(3)}
    web = web_for(locs=locs, extra=extra)
    _, rows, _, _ = run(tmp_path, web)
    r = rows[0]
    assert r['Status'] == 'Check these', r
    assert all(r[f'Round {i}'].startswith('fail') for i in (1, 2, 3)), r
    assert r['Note'].count('R') >= 3 and r['File'].startswith('Check these')
    assert (tmp_path / 'PDFs' / r['File']).is_file()
    assert not any(p.suffix == '.pdf' for p in (tmp_path / 'PDFs').iterdir())


def test_single_source_still_records_three_rounds(tmp_path):
    wrong = ok_pdf(make_pdf(title='A Totally Different Paper About Databases', authors='Ann Smith',
                            footer='doi:10.1145/9.9', pages=7), PDF_URL)
    web = web_for(pdf=[wrong, wrong, wrong])
    _, rows, _, _ = run(tmp_path, web)
    r = rows[0]
    assert r['Round 1'].startswith('fail') and r['Round 2'] == 'same file again' and r['Round 3'] == 'same file again', r
    assert r['Status'] == 'Check these'


def test_acm_only_is_listed_not_requested(tmp_path):
    doi = '10.1145/3626252.3630874'
    web = web_for(doi=doi, locs=[{'url_for_pdf': 'https://dl.acm.org/doi/pdf/' + doi, 'version': 'publishedVersion', 'host_type': 'publisher'}])
    _, rows, _, _ = run(tmp_path, web, text=doi)
    assert rows[0]['Status'] == 'Not downloadable automatically' and 'ACM' in rows[0]['Note'], rows[0]
    assert not any('acm.org' in u for u in web.log)


def test_no_free_copy(tmp_path):
    web = web_for(locs=[])
    _, rows, _, _ = run(tmp_path, web)
    assert rows[0]['Status'] == 'No free legal copy', rows[0]


def test_site_gives_web_page_not_file(tmp_path):
    web = web_for(pdf=html_page(PDF_URL))
    _, rows, _, _ = run(tmp_path, web)
    assert rows[0]['Status'] == 'Not downloadable automatically' and 'no file' in rows[0]['Note'], rows[0]
    assert all(r == '' for r in (rows[0]['Round 1'], rows[0]['Round 2'], rows[0]['Round 3']))   # no round used


def test_publisher_fail_then_preprint_pass_keeps_both(tmp_path):
    wrong_pub = make_pdf(title='Some Other Published Article Entirely', authors='Ann Smith', footer='doi:10.1145/9.9', pages=7)
    locs = [{'url_for_pdf': PDF_URL, 'version': 'publishedVersion', 'host_type': 'publisher'},
            {'url_for_pdf': 'https://arxiv.org/pdf/2301.00001', 'version': 'submittedVersion', 'host_type': 'repository'}]
    extra = {'https://arxiv.org/pdf/2301.00001': ok_pdf(make_pdf(title=TITLE, authors='Jane Cruz, Bob Lee', pages=7))}
    web = web_for(pdf=ok_pdf(wrong_pub, PDF_URL), locs=locs, extra=extra,
                  cr=crossref_record(DOI, TITLE, pages=''))
    _, rows, _, _ = run(tmp_path, web)
    r = rows[0]
    assert r['Status'] == 'Verified' and r['Proof'] == 'PASS-B' and r['File'].endswith('_preprint.pdf'), r
    assert 'please compare' in r['Note']
    assert any(p.suffix == '.pdf' for p in (tmp_path / 'PDFs' / 'Check these').iterdir())


def test_resume_second_run_fetches_nothing(tmp_path):
    e, rows, _, _ = run(tmp_path, web_for())
    web2 = web_for()
    e2, rows2, _, _ = run(tmp_path, web2)
    assert rows2[0]['Status'] == 'Already verified' and web2.log == [], (rows2[0], web2.log)


def test_check_only_downloads_nothing(tmp_path):
    web = web_for()
    e, _ = engine(tmp_path, web)
    rows = e.run(E.parse_input(DOI), check_only=True)
    assert rows[0]['Status'] == 'Available' and PDF_URL in rows[0]['Source']
    assert PDF_URL not in web.log and not list((tmp_path / 'PDFs').glob('*.pdf'))


def test_retracted_is_flagged_in_name_and_report(tmp_path):
    web = web_for(cr=crossref_record(DOI, TITLE, pages='1-7', retracted=True))
    _, rows, _, _ = run(tmp_path, web)
    assert rows[0]['Retracted'] == 'RETRACTED' and rows[0]['File'].endswith('_RETRACTED.pdf'), rows[0]


def test_scanned_goes_to_check_these_as_not_checkable(tmp_path):
    web = web_for(pdf=ok_pdf(scanned_pdf(), PDF_URL))
    _, rows, _, _ = run(tmp_path, web)
    assert rows[0]['Status'] == 'Not checkable: scanned' and rows[0]['File'].startswith('Check these'), rows[0]


def test_registry_unreachable_is_not_checked_and_nothing_downloaded(tmp_path):
    web = web_for(extra={cr_url(DOI): [S.Response(0, 'x', reason='timed out')] * 4})
    _, rows, _, _ = run(tmp_path, web)
    assert rows[0]['Status'] == 'Not checked' and PDF_URL not in web.log


def test_stop_marks_remaining_rows(tmp_path):
    done = {'n': 0}
    web = web_for()
    _, rows, items, _ = run(tmp_path, web, text=f'{DOI}\n10.3390/other1\n10.3390/other2',
                            stop=lambda: done['n'] >= 1, on_row=lambda r: done.__setitem__('n', done['n'] + 1))
    assert rows[0]['Status'] == 'Verified' and rows[1]['Status'] == 'Stopped' and rows[2]['Status'] == 'Stopped'
    assert E.totals_agree(items, rows)


def test_totals_and_prisma(tmp_path):
    web = web_for()
    _, rows, items, _ = run(tmp_path, web, text=f'{DOI}\nn.r.\n{DOI.upper()}')
    assert [r['Status'] for r in rows] == ['Verified', 'Not a DOI', 'Duplicate']
    assert E.totals_agree(items, rows)
    bad = [dict(r) for r in rows]
    bad[0]['Status'] = ''
    assert not E.totals_agree(items, bad)
    p = E.prisma(rows)
    assert p == {'sought': 1, 'retrieved': 1, 'not_retrieved': 0, 'reasons': {}}


def test_export_references_parse_back(tmp_path):
    e, rows, _, _ = run(tmp_path, web_for())
    ris, bib = E.export_references(e.journal, tmp_path)
    t = ris.read_text(encoding='utf-8')
    assert 'TY  - JOUR' in t and f'DO  - {DOI}' in t and 'AU  - Cruz' in t
    b = bib.read_text(encoding='utf-8')
    assert b.startswith('@article{cruz2024') and f'doi = {{{DOI}}}' in b


def test_file_names_are_windows_safe():
    f = S.Facts(doi='10.1/x', title='What: is "AI"? A/B test <x> | y*', authors=['García'], year='2024')
    name = E.file_name('{author}_{year}_{title}', f, '10.1/x', 'publishedVersion', False, set())
    assert not E.BAD_CHARS.search(name) and name.startswith('Garcia_2024_'), name
    assert E.file_name('{author}', S.Facts(doi='d', title='', authors=['CON']), 'd', 'publishedVersion', False, set()) == 'Con_.pdf'


def test_letters_name_the_right_addresses():
    t = E.acm_letter(['10.1145/1.2'])
    assert t.startswith('To: dl-info@hq.acm.org') and 'Cc: permissions@acm.org' in t
    r = E.request_texts({'Title (registry)': 'T', 'DOI': DOI, 'First author': 'Cruz'})
    assert DOI in r['author'] and DOI in r['library']


def test_no_browser_or_application_is_ever_launched():
    code = ''
    for name in ('engine.py', 'sources.py', 'verify.py', 'settings.py', 'importer.py', 'app.pyw'):
        p = os.path.join(os.path.dirname(HERE), name)
        if os.path.exists(p):
            code += open(p, encoding='utf-8').read()
    for forbidden in ('os.startfile', 'webbrowser', 'subprocess.Popen', 'playwright', 'selenium'):
        assert forbidden not in code, forbidden


@pytest.mark.parametrize('mutate,test', [
    (lambda: setattr(E, 'MAX_ROUNDS', 1), test_single_source_still_records_three_rounds),
    (lambda: setattr(E, 'totals_agree', lambda i, r: True), test_totals_and_prisma),
    (lambda: setattr(E, 'REFETCH_WAIT', 0), test_damaged_then_fresh_copy_passes_in_round_2),
])
def test_mutant_is_caught(mutate, test, tmp_path):
    import importlib
    importlib.reload(E)
    mutate()
    try:
        with pytest.raises(AssertionError):
            test(tmp_path)
    finally:
        importlib.reload(E)
