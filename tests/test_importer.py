"""Import: files obtained elsewhere are matched, checked and filed; strangers are left alone."""
import io
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import engine as E                                                   # noqa: E402
import importer as I                                                 # noqa: E402
import sources as S                                                  # noqa: E402
from fakeweb import FakeWeb, ok_json, crossref_record, q             # noqa: E402
from pdfmake import make_pdf                                         # noqa: E402

EMAIL = 'tester@example.org'
DOI = '10.1145/3626252.3630874'
TITLE = 'Implications of ChatGPT for Data Science Education Practice'


def setup(tmp):
    web = FakeWeb({S.Registry.CROSSREF + q(DOI) + '?mailto=tester%40example.org': ok_json(crossref_record(DOI, TITLE, pages='1230-1236'))},
                  patterns={'query.bibliographic': ok_json({'message': {'items': []}})})
    e = E.Engine(tmp, EMAIL, web=web, sleep=lambda s: None)
    (tmp / 'PDFs' / 'Check these').mkdir(parents=True)
    return e, web


def test_zip_and_loose_pdf_are_matched_checked_filed(tmp_path):
    e, web = setup(tmp_path)
    good = make_pdf(title=TITLE, authors='Jane Cruz', footer=f'https://doi.org/{DOI}', pages=7)
    stranger = make_pdf(title='An Unrelated Paper About Rivers and Lakes', authors='Ann Smith', footer='doi:10.9/x', pages=3)
    (tmp_path / 'mine.pdf').write_bytes(good)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('acm_bulk/stranger.pdf', stranger)
    (tmp_path / 'bulk.zip').write_bytes(buf.getvalue())
    rows = I.import_files(e, [tmp_path / 'mine.pdf', tmp_path / 'bulk.zip'], [DOI])
    by = {r['File given']: r for r in rows}
    assert by['mine.pdf']['Status'] == 'Verified' and by['mine.pdf']['Proof'] == 'PASS-A', by['mine.pdf']
    assert (tmp_path / 'PDFs' / by['mine.pdf']['Filed as']).is_file()
    assert by['bulk.zip/stranger.pdf']['Status'] == 'Not in your list'
    assert (tmp_path / 'mine.pdf').is_file() and (tmp_path / 'bulk.zip').is_file()      # originals untouched
    assert not any('acm.org' in u for u in web.log)


def test_matched_by_title_only_goes_to_check_these_when_incomplete(tmp_path):
    e, _ = setup(tmp_path)
    short = make_pdf(title=TITLE, authors='Jane Cruz', pages=1)        # title matches, but 1 page
    (tmp_path / 'short.pdf').write_bytes(short)
    rows = I.import_files(e, [tmp_path / 'short.pdf'], [DOI])
    assert rows[0]['DOI'] == DOI and rows[0]['Status'] == 'Check these', rows[0]
    assert rows[0]['Filed as'].startswith('Check these')
