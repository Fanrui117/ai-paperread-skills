"""在线检索：按关键词在 arXiv / Crossref 找候选论文，供 collect 入库。

- search_arxiv(query, limit)：arXiv API（Atom）
- search_crossref(query, limit)：Crossref REST
- merge/dedupe：按 DOI / arXiv ID / 归一化标题去重
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from .ingest import Metadata, _clean, _norm

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"


@dataclass
class Candidate:
    source: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    pdf_url: str = ""
    abstract: str = ""

    def to_meta(self) -> Metadata:
        return Metadata(
            title=self.title,
            authors=self.authors,
            year=self.year,
            publication=self.venue or ("arXiv" if self.arxiv_id else ""),
            doi=self.doi,
            url=self.url,
            abstract=self.abstract,
            arxiv_id=self.arxiv_id,
            pdf_url=self.pdf_url,
        )

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "abstract": self.abstract,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(
            source=str(d.get("source", "")),
            title=str(d.get("title", "")),
            authors=list(d.get("authors") or []),
            year=str(d.get("year", "")),
            venue=str(d.get("venue", "")),
            doi=str(d.get("doi", "")),
            arxiv_id=str(d.get("arxiv_id", "")),
            url=str(d.get("url", "")),
            pdf_url=str(d.get("pdf_url", "")),
            abstract=str(d.get("abstract", "")),
        )


def _key(c: Candidate) -> str:
    if c.doi:
        return "doi:" + _norm(c.doi)
    if c.arxiv_id:
        return "arxiv:" + c.arxiv_id
    return "title:" + re.sub(r"[^a-z0-9]+", "", _norm(c.title))[:80]


def parse_arxiv_atom(xml_text: str) -> list[Candidate]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    out: list[Candidate] = []
    for entry in root.findall("a:entry", ns):
        raw_id = entry.findtext("a:id", default="", namespaces=ns)
        m = re.search(r"abs/(\d{4}\.\d{4,5})(?:v\d+)?", raw_id)
        arxiv_id = m.group(1) if m else ""
        title = _clean(entry.findtext("a:title", default="", namespaces=ns))
        authors = [n.text or "" for n in entry.findall("a:author/a:name", ns)]
        published = entry.findtext("a:published", default="", namespaces=ns)
        abstract = _clean(entry.findtext("a:summary", default="", namespaces=ns))
        doi = entry.findtext("{http://arxiv.org/schemas/atom}doi", default="", namespaces=None) or ""
        out.append(
            Candidate(
                source="arxiv",
                title=title,
                authors=authors,
                year=published[:4],
                venue="arXiv",
                doi=doi,
                arxiv_id=arxiv_id,
                url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else raw_id,
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "",
                abstract=abstract,
            )
        )
    return out


def parse_crossref_json(data: dict) -> list[Candidate]:
    out: list[Candidate] = []
    for msg in (data.get("message") or {}).get("items", []):
        title = (msg.get("title") or [""])[0]
        authors = []
        for a in msg.get("author", []):
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x).strip()
            if name:
                authors.append(name)
        year = ""
        for key in ("published-print", "published-online", "issued"):
            parts = (msg.get(key) or {}).get("date-parts")
            if parts and parts[0] and parts[0][0]:
                year = str(parts[0][0])
                break
        doi = msg.get("DOI", "")
        out.append(
            Candidate(
                source="crossref",
                title=_clean(title),
                authors=authors,
                year=year,
                venue=(msg.get("container-title") or [""])[0],
                doi=doi,
                url=msg.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                pdf_url="",
                abstract=re.sub(r"<[^>]+>", "", msg.get("abstract") or ""),
            )
        )
    return out


def search_arxiv(query: str, limit: int = 10) -> list[Candidate]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
    }
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        r = client.get(ARXIV_API, params=params)
        r.raise_for_status()
    return parse_arxiv_atom(r.text)


def search_crossref(query: str, limit: int = 10) -> list[Candidate]:
    params = {"query.bibliographic": query, "rows": limit}
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        r = client.get(CROSSREF_API, params=params)
        r.raise_for_status()
        return parse_crossref_json(r.json())


def search(query: str, limit: int = 10, source: str = "all") -> list[Candidate]:
    """source: arxiv | crossref | all；结果去重（先出现的优先）。"""
    results: list[Candidate] = []
    if source in ("arxiv", "all"):
        try:
            results.extend(search_arxiv(query, limit))
        except Exception:  # noqa: BLE001
            if source == "arxiv":
                raise
    if source in ("crossref", "all"):
        try:
            results.extend(search_crossref(query, limit))
        except Exception:  # noqa: BLE001
            if source == "crossref":
                raise
    seen: set[str] = set()
    merged: list[Candidate] = []
    for c in results:
        k = _key(c)
        if k in seen:
            continue
        seen.add(k)
        merged.append(c)
    return merged[:limit]
