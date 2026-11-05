"""PDF 有效性校验（本地 --pdf / 下载回退用）。"""
from paperread.ingest import _valid_pdf


def test_valid_pdf_accepts_eof():
    assert _valid_pdf(b"%PDF-1.4\ncontent\n%%EOF") is True


def test_valid_pdf_rejects_html():
    assert _valid_pdf(b"<html>not a pdf</html>") is False
    assert _valid_pdf(b"") is False


def test_valid_pdf_accepts_real_pdf_bytes():
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    assert _valid_pdf(data) is True
