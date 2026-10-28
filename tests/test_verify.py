"""Hostile tests for verify.py: each one is a way the check could be fooled."""
import os
import sys

import pymupdf
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import verify as V                                   # noqa: E402
from pdfmake import make_pdf, scanned_pdf            # noqa: E402

DOI = '10.1145/3626252.3630874'
TITLE = 'Implications of ChatGPT for Data Science Education'


def facts(**kw):
    base = dict(doi=DOI, title=TITLE, authors=['Cruz', 'Lee'], year='2024',
                page_first=1230, page_last=1236, kind='proceedings-article')
    base.update(kw)
    return V.Facts(**base)


def test_pass_a_doi_in_footer():
    data = make_pdf(title=TITLE, authors='Jane Cruz, Bob Lee', footer=f'https://doi.org/{DOI}', pages=7)
    v = V.verify(data, facts())
    assert v.ok and v.proof == 'PASS-A', v


def test_pass_b_title_and_author_no_doi():
    data = make_pdf(title=TITLE, authors='Jane Cruz, Bob Lee', pages=7)
    v = V.verify(data, facts(page_first=None, page_last=None))
    assert v.ok and v.proof == 'PASS-B', v


def test_wrong_paper_other_doi_fails():
    data = make_pdf(title='Something Completely Different About Databases', authors='Ann Smith',
                    footer='https://doi.org/10.1145/9999999.9999999', pages=7)
    v = V.verify(data, facts())
    assert not v.ok and v.reason_code in ('other_doi', 'no_title'), v


def test_doi_prefix_does_not_match_longer_doi():
    # chapter _5 against a file that prints chapter _52
    f = facts(doi='10.1007/978-3-031-64315-6_5', title=TITLE, page_first=None, page_last=None)
    data = make_pdf(title=TITLE, authors='Jane Cruz', footer='https://doi.org/10.1007/978-3-031-64315-6_52', pages=7)
    v = V.verify(data, f)
    assert not v.ok and v.reason_code == 'other_doi', v


def test_short_surname_is_whole_word_only():
    # author 'Li' must not be found inside 'literature'
    f = facts(authors=['Li'], page_first=None, page_last=None)
    data = make_pdf(title=TITLE, authors='The literature review team', pages=7)
    v = V.verify(data, f)
    assert not v.ok and v.reason_code == 'no_author', v


def test_correction_notice_fails_even_with_doi():
    data = make_pdf(title='Correction to: ' + TITLE, authors='Jane Cruz', footer=f'https://doi.org/{DOI}', pages=2)
    v = V.verify(data, facts())
    assert not v.ok and v.reason_code == 'notice', v


def test_twin_blocks_pass_b_but_not_pass_a():
    f = facts(twins=['10.1016/j.caeai.2024.100290'], page_first=None, page_last=None)
    data = make_pdf(title=TITLE, authors='Jane Cruz', pages=7)
    assert V.verify(data, f).reason_code == 'twin_exists'
    data2 = make_pdf(title=TITLE, authors='Jane Cruz', footer=f'doi:{DOI}', pages=7)
    assert V.verify(data2, f).proof == 'PASS-A'
    data3 = make_pdf(title=TITLE, authors='Jane Cruz', footer='doi:10.1016/j.caeai.2024.100290', pages=7)
    assert V.verify(data3, f).reason_code == 'twin'


def test_accent_and_markup_normalising():
    f = facts(title='Teaching <i>AI</i> &amp; Learning: a study of novice programmers in class',
              authors=['García'], page_first=None, page_last=None)
    data = make_pdf(title='Teaching AI & Learning: a study of novice programmers in class',
                    authors='M. Garc´ıa', pages=7)
    v = V.verify(data, f)
    assert v.ok, v


def test_scanned_is_not_checkable():
    v = V.verify(scanned_pdf(), facts())
    assert not v.ok and v.reason_code == 'no_text', v


def test_truncated_file_is_damaged():
    data = make_pdf(title=TITLE, authors='Jane Cruz', footer=f'doi:{DOI}', pages=7)
    cut = data[: int(len(data) * 0.8)]
    v = V.verify(cut, facts())
    assert not v.ok and v.reason_code == 'damaged', v


def test_one_page_file_is_too_short():
    data = make_pdf(title=TITLE, authors='Jane Cruz', footer=f'doi:{DOI}', pages=1)
    v = V.verify(data, facts(page_first=None, page_last=None))
    assert not v.ok and v.reason_code == 'too_short', v


def test_page_range_mismatch_and_not_checkable():
    data = make_pdf(title=TITLE, authors='Jane Cruz', footer=f'doi:{DOI}', pages=3)
    v = V.verify(data, facts(page_first=1, page_last=12))          # 3 pages vs 12
    assert not v.ok and v.reason_code == 'pages', v
    v2 = V.verify(data, facts(page_first=1245, page_last=1245))    # MDPI article number
    assert v2.ok and 'not checkable' in v2.page_note, v2


def test_cover_sheet_moves_window():
    data = make_pdf(title=TITLE, authors='Jane Cruz', footer=f'doi:{DOI}', pages=7,
                    cover=f'Latest updates: https://dl.acm.org/doi/{DOI}\n{TITLE}')
    v = V.verify(data, facts())
    assert v.ok and v.cover_sheet, v


def test_generic_title_cannot_pass_b():
    f = facts(title='Editorial', page_first=None, page_last=None)
    data = make_pdf(title='Editorial', authors='Jane Cruz', pages=3)
    assert V.verify(data, f).reason_code == 'generic_title'


def test_password_locked_is_damaged():
    doc = pymupdf.open(stream=make_pdf(title=TITLE, pages=3), filetype='pdf')
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='x', owner_pw='y')
    assert 'password-locked' in ' '.join(V.damage_report(locked))


def test_doi_pattern_boundaries():
    p = V.doi_pattern('10.1145/3626252')
    assert not p.search('10.1145/3626252.3630874')
    assert p.search('see 10.1145/3626252 for details')


# ---- mutants: each guard above must be SEEN to fail when broken
@pytest.mark.parametrize('mutate,test', [
    (lambda: setattr(V, 'doi_pattern', lambda d: __import__('re').compile(__import__('re').escape(V.norm_doi(d)))),
     test_doi_pattern_boundaries),
    (lambda: setattr(V, 'NOTICE_WORDS', __import__('re').compile(r'(?!x)x')), test_correction_notice_fails_even_with_doi),
    (lambda: setattr(V, 'damage_report', lambda d, c=None: []), test_truncated_file_is_damaged),
    (lambda: setattr(V, 'author_on_page', lambda f, p: True), test_short_surname_is_whole_word_only),
])
def test_mutant_is_caught(mutate, test, monkeypatch):
    import importlib
    importlib.reload(V)
    mutate()
    try:
        with pytest.raises(AssertionError):
            test()
    finally:
        importlib.reload(V)
