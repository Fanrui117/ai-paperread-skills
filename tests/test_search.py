"""在线检索解析（离线 fixture，不发网络请求）。"""
from paperread.search import Candidate, parse_arxiv_atom, parse_crossref_json

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>  Point Tracking   Revisited </title>
    <published>2024-01-22T10:00:00Z</published>
    <summary>We study point tracking.</summary>
    <author><name>Jane Doe</name></author>
    <author><name>Richard Roe</name></author>
    <link rel="related" title="pdf" href="http://arxiv.org/pdf/2401.12345v2"/>
  </entry>
</feed>
"""

CROSSREF_JSON = {
    "message": {
        "items": [
            {
                "title": ["A Survey of Point Tracking"],
                "author": [{"given": "Ann", "family": "Poe"}],
                "issued": {"date-parts": [[2023, 5, 1]]},
                "container-title": ["CVPR"],
                "DOI": "10.1000/abc",
                "URL": "https://doi.org/10.1000/abc",
                "abstract": "<jats:p>Survey.</jats:p>",
            }
        ]
    }
}


def test_parse_arxiv_atom():
    items = parse_arxiv_atom(ARXIV_XML)
    assert len(items) == 1
    c = items[0]
    assert c.arxiv_id == "2401.12345"
    assert c.title == "Point Tracking Revisited"
    assert c.authors == ["Jane Doe", "Richard Roe"]
    assert c.year == "2024"
    assert c.pdf_url.endswith("/pdf/2401.12345")


def test_parse_crossref_json():
    items = parse_crossref_json(CROSSREF_JSON)
    assert len(items) == 1
    c = items[0]
    assert c.doi == "10.1000/abc"
    assert c.year == "2023"
    assert c.venue == "CVPR"
    assert c.authors == ["Ann Poe"]


def test_candidate_roundtrip():
    c = Candidate(source="arxiv", title="T", authors=["A B"], year="2024", arxiv_id="2401.00001")
    d = c.to_dict()
    c2 = Candidate.from_dict(d)
    assert c2 == c
    assert c2.to_meta().arxiv_id == "2401.00001"
