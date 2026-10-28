"""check_before_publish catches planted secrets; the window opens and runs on the fake network."""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import check_before_publish as CP                                    # noqa: E402
from fakeweb import FakeWeb, ok_json, ok_pdf, crossref_record, unpaywall_record, q   # noqa: E402
from pdfmake import make_pdf                                         # noqa: E402
import sources as S                                                  # noqa: E402


def copy_tree(tmp):
    dst = tmp / 'proj'
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns('__pycache__', '_v1_backup', '.pytest_cache', 'PDFs',
                                                             'Report*.csv', 'download_log.json', 'Manifest.csv', '*.lnk'))
    return dst


def test_publish_check_clean_then_planted(tmp_path):
    dst = copy_tree(tmp_path)
    assert CP.problems(dst) == [], CP.problems(dst)
    planted = 'somebody' + '@' + 'gmail.com'            # built at run time so this file stays clean
    (dst / 'notes.txt').write_text('contact me at ' + planted, encoding='utf-8')
    (dst / 'settings.json').write_text('{}', encoding='utf-8')
    (dst / 'engine.py').write_text((dst / 'engine.py').read_text(encoding='utf-8') + '\nAPI_KEY = "abcdefghijklmnop1234"\n', encoding='utf-8')
    found = CP.problems(dst)
    kinds = ' '.join(found)
    assert planted in kinds and 'settings file' in kinds and 'key or token' in kinds, found


def test_no_console_or_application_calls_in_window():
    import re
    code = open(os.path.join(ROOT, 'app.pyw'), encoding='utf-8').read()
    assert not re.search(r'(?<![\w.])input\(', code)             # the console builtin, not parse_input(
    for bad in ('sys.stdout', 'os.startfile', 'webbrowser', 'subprocess'):
        assert bad not in code, bad


@pytest.mark.skipif(os.environ.get('NO_DISPLAY') == '1', reason='no display')
def test_window_runs_a_download_on_fake_network(tmp_path, monkeypatch):
    import app as A
    doi = '10.3390/su16031245'
    title = 'Generative AI Scaffolding for Novice Programmers in Introductory Courses'
    pdf = make_pdf(title=title, authors='Jane Cruz, Bob Lee', footer=f'https://doi.org/{doi}', pages=7)
    routes = {S.Registry.CROSSREF + q(doi) + '?mailto=tester%40example.org': ok_json(crossref_record(doi, title, pages='1-7')),
              S.Finder.UNPAYWALL + q(doi) + '?email=tester%40example.org': ok_json(unpaywall_record(doi, [
                  {'url_for_pdf': 'https://pub.example/p.pdf', 'version': 'publishedVersion', 'host_type': 'publisher'}])),
              'https://pub.example/p.pdf': ok_pdf(pdf, 'https://pub.example/p.pdf')}
    webs = []

    def factory(email):
        w = FakeWeb(routes, patterns={'query.bibliographic': ok_json({'message': {'items': []}})}, email=email)
        webs.append(w)
        return w
    monkeypatch.setattr(A.ST, 'load', lambda: {'email': 'tester@example.org', 'keys': {}, 'template': '', 'resolvers': []})
    monkeypatch.setattr(A.messagebox, 'showinfo', lambda *a, **k: None)
    app = A.App(folder=tmp_path, web_factory=factory)
    try:
        app.paste.insert('1.0', doi + '\nn.r.\n')
        app.start_download()
        for _ in range(300):
            app.update()
            if app.progress.get().startswith('Done') or 'TOTALS' in app.progress.get():
                break
            import time
            time.sleep(0.05)
        assert app.progress.get().startswith('Done'), app.progress.get()
        vals = [app.tree.item(i)['values'] for i in app.tree.get_children()]
        assert vals[0][1] == 'Verified' and vals[1][1] == 'Not a DOI', vals
        assert 'reports sought for retrieval 1' in app.prisma_var.get()
        assert (tmp_path / 'Report.csv').is_file()
        assert all('acm.org' not in u for w in webs for u in w.log)
    finally:
        app.destroy()
