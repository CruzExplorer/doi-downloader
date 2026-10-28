"""Build small PDFs in memory for the tests. Nothing here is downloaded."""
import os

import pymupdf

_ARIAL = r'C:\Windows\Fonts\arial.ttf'
FONT = dict(fontname='F0', fontfile=_ARIAL) if os.path.exists(_ARIAL) else {}


def make_pdf(title='A Study of Testing Things in Programming Education', authors='Jane Cruz, Bob Lee',
             body='Body text ' * 200, header='', footer='', pages=4, title_size=18,
             cover=None, meta=None, extra_pages_text=None, blank_pages=()):
    doc = pymupdf.open()
    if cover:
        p = doc.new_page()
        p.insert_text((72, 100), cover, fontsize=11)
    for i in range(pages):
        p = doc.new_page()
        if i + 1 in blank_pages:
            continue
        if header:
            p.insert_text((72, 40), header, fontsize=8)
        if i == 0:
            p.insert_textbox(pymupdf.Rect(72, 90, 540, 150), title, fontsize=title_size)
            p.insert_text((72, 160), authors, fontsize=11, **FONT)
            p.insert_textbox(pymupdf.Rect(72, 190, 540, 760), body[:2000], fontsize=10)
        else:
            txt = (extra_pages_text or {}).get(i + 1, body[:2000])
            p.insert_textbox(pymupdf.Rect(72, 90, 540, 760), txt, fontsize=10)
        if footer:
            p.insert_text((72, p.rect.height - 40), footer, fontsize=8)
    if meta:
        doc.set_metadata(meta)
    data = doc.tobytes()
    doc.close()
    return data


def scanned_pdf(pages=3):
    doc = pymupdf.open()
    for _ in range(pages):
        p = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
        pix.set_rect(pix.irect, (200, 200, 200))
        p.insert_image(pymupdf.Rect(72, 72, 300, 300), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data
