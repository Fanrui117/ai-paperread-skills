"""arXiv HTML 元数据回退解析（离线）。"""
from paperread.ingest import parse_arxiv_html

HTML = """
<html><head>
<meta name="citation_title" content="A Great Paper" />
<meta name="citation_author" content="Doe, Jane" />
<meta name="citation_author" content="Roe, Richard" />
<meta name="citation_date" content="2024/01/22" />
</head><body>
<blockquote class="abstract mathjax">
  <span class="descriptor">Abstract:</span> We study things.
</blockquote>
</body></html>
"""


def test_parse_arxiv_html():
    meta = parse_arxiv_html(HTML, "2401.12345")
    assert meta.title == "A Great Paper"
    assert meta.authors == ["Doe, Jane", "Roe, Richard"]
    assert meta.year == "2024"
    assert "We study things" in meta.abstract
    assert meta.arxiv_id == "2401.12345"
    assert meta.pdf_url.endswith("/pdf/2401.12345")
    assert meta.publication == "arXiv"
