"""单篇查找 / 入库 / 处理。

- search_papers(lib, query)：在已加载的库里按 key / 标题 / DOI / 作者模糊查找
- fetch_metadata(identifier)：arXiv / DOI / URL -> 元数据
- ingest_paper(cfg, identifier)：通过 Zotero Connector 写入 Zotero（可选），并返回新条目 key
"""
from __future__ import annotations

import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import Config
from .zotero import Paper, ZoteroLibrary, load_library_auto
from .zotero_connector import ConnectorClient, ConnectorError, build_item

ARXIV_RE = re.compile(r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
BARE_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
CROSSREF_API = "https://api.crossref.org/works/"
ARXIV_API = "https://export.arxiv.org/api/query?id_list="


@dataclass
class Metadata:
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    publication: str = ""
    doi: str = ""
    url: str = ""
    abstract: str = ""
    arxiv_id: str = ""
    pdf_url: str = ""


# ---------- 库内查找 ----------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _clean(s: str) -> str:
    """只折叠空白，保留大小写（标题/摘要展示用）。"""
    return re.sub(r"\s+", " ", s or "").strip()


def search_papers(lib: ZoteroLibrary, query: str) -> list[Paper]:
    q = _norm(query)
    if not q:
        return []
    hits: list[tuple[int, Paper]] = []
    for paper in lib.papers:
        fields = {
            "key": _norm(paper.key),
            "title": _norm(paper.title),
            "doi": _norm(paper.doi),
            "authors": _norm("; ".join(paper.creators)),
        }
        score = 0
        if q == fields["key"]:
            score = 100
        elif q == fields["doi"] or (q and q in fields["doi"]):
            score = 80
        elif q in fields["title"]:
            score = 60
        elif q in fields["authors"]:
            score = 40
        if score:
            hits.append((score, paper))
    hits.sort(key=lambda x: (-x[0], x[1].title.lower()))
    return [p for _, p in hits]


def resolve_one(lib: ZoteroLibrary, query: str) -> tuple[Paper | None, list[Paper]]:
    """唯一命中返回 (paper, []); 多命中返回 (None, matches)。"""
    matches = search_papers(lib, query)
    if len(matches) == 1:
        return matches[0], []
    return None, matches


# ---------- 元数据抓取 ----------

def parse_identifier(raw: str) -> tuple[str, str]:
    s = raw.strip()
    m = ARXIV_RE.search(s)
    if m:
        return "arxiv", m.group(1)
    m = BARE_ARXIV_RE.match(s)
    if m:
        return "arxiv", m.group(1)
    if DOI_RE.match(s):
        return "doi", s
    if s.lower().startswith(("http://", "https://")):
        return "url", s
    return "query", s


def _fetch_arxiv_html(arxiv_id: str) -> Metadata:
    """arXiv API 被限流（429）时，从 abs 页面解析 citation_* 元数据。"""
    r = httpx.get(f"https://arxiv.org/abs/{arxiv_id}", follow_redirects=True, timeout=30)
    r.raise_for_status()
    html = r.text

    def meta(name: str) -> str:
        m = re.search(rf'<meta name="{name}" content="([^"]*)"', html)
        return _clean(m.group(1)) if m else ""

    title = meta("citation_title")
    authors = re.findall(r'<meta name="citation_author" content="([^"]*)"', html)
    date = meta("citation_date") or meta("citation_online_date")
    abstract = ""
    m = re.search(r'<blockquote class="abstract[^"]*">(.*?)</blockquote>', html, re.S)
    if m:
        abstract = _clean(re.sub(r"<[^>]+>", " ", m.group(1)))
    if not title:
        raise RuntimeError(f"无法从 arXiv 页面解析 {arxiv_id}")
    return Metadata(
        title=title,
        authors=[a.strip() for a in authors],
        year=date[:4],
        publication="arXiv",
        url=f"https://arxiv.org/abs/{arxiv_id}",
        abstract=abstract,
        arxiv_id=arxiv_id,
        pdf_url=f"https://www.arxiv.org/pdf/{arxiv_id}",
    )


def fetch_arxiv(arxiv_id: str) -> Metadata:
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.get(ARXIV_API + urllib.parse.quote(arxiv_id))
            r.raise_for_status()
    except Exception:  # noqa: BLE001  429/超时等情况走 HTML 回退
        return _fetch_arxiv_html(arxiv_id)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    entry = root.find("a:entry", ns)
    if entry is None:
        raise RuntimeError(f"arXiv 查不到 {arxiv_id}")
    title = _clean(entry.findtext("a:title", default="", namespaces=ns))
    authors = [n.text or "" for n in entry.findall("a:author/a:name", ns)]
    published = entry.findtext("a:published", default="", namespaces=ns)
    abstract = _clean(entry.findtext("a:summary", default="", namespaces=ns))
    doi = entry.findtext("{http://arxiv.org/schemas/atom}doi", default="", namespaces=None) or ""
    return Metadata(
        title=title,
        authors=authors,
        year=published[:4],
        publication="arXiv",
        doi=doi,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        abstract=abstract,
        arxiv_id=arxiv_id,
        pdf_url=f"https://www.arxiv.org/pdf/{arxiv_id}",
    )


def fetch_crossref(doi: str) -> Metadata:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        r = client.get(CROSSREF_API + urllib.parse.quote(doi))
        r.raise_for_status()
        msg = r.json()["message"]
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
    publication = (msg.get("container-title") or [""])[0]
    abstract = re.sub(r"<[^>]+>", "", msg.get("abstract") or "")
    url = msg.get("URL") or f"https://doi.org/{doi}"
    return Metadata(
        title=_clean(title),
        authors=authors,
        year=year,
        publication=publication,
        doi=doi,
        url=url,
        abstract=_clean(abstract),
    )


def fetch_metadata(identifier: str) -> Metadata:
    kind, value = parse_identifier(identifier)
    if kind == "arxiv":
        return fetch_arxiv(value)
    if kind == "doi":
        return fetch_crossref(value)
    if kind == "url":
        # 只做最小处理：当作网页条目保存，标题用 URL 末段
        slug = value.rstrip("/").split("/")[-1] or value
        return Metadata(title=slug.replace("-", " "), url=value)
    raise ValueError(f"无法识别的标识符：{identifier}（支持 arXiv ID、DOI、URL）")


# ---------- 入库 ----------

def _valid_pdf(data: bytes) -> bool:
    if not data or data[:4] != b"%PDF":
        return False
    if data.rstrip().endswith(b"%%EOF"):
        return True
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return doc.page_count > 0
    except Exception:  # noqa: BLE001
        return False


def _curl_download(url: str, out, max_time: int, resume: bool = False) -> bytes | None:
    import subprocess

    cmd = ["curl", "-sL", "--max-time", str(max_time), "-A", "Mozilla/5.0"]
    if resume and out.exists():
        cmd += ["-C", "-"]
    cmd += ["-o", str(out), url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=max_time + 30)
    except Exception:  # noqa: BLE001
        return None
    if not out.is_file():
        return None
    data = out.read_bytes()
    return data if _valid_pdf(data) else None


def _download_pdf(meta: Metadata, client: ConnectorClient) -> bytes | None:
    if not meta.pdf_url:
        return None
    import tempfile

    out = Path(tempfile.mkdtemp(prefix="paperread-pdf-")) / "paper.pdf"
    # 1) curl 分阶段下载（慢链路下先短超时，再断点续传补全）
    for attempt, (max_time, resume) in enumerate(((300, False), (600, True), (900, True))):
        data = _curl_download(meta.pdf_url, out, max_time, resume=resume)
        if data:
            return data
    # 2) httpx 兜底
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as http:
            r = http.get(meta.pdf_url)
            r.raise_for_status()
            if _valid_pdf(r.content):
                return r.content
    except Exception:  # noqa: BLE001
        pass
    return None


def ingest_paper(
    cfg: Config, identifier: str, collection: str | None = None, pdf_bytes: bytes | None = None
) -> tuple[str, Metadata]:
    """把论文写入 Zotero（connector API），返回新条目的 Zotero key。"""
    return ingest_metadata(cfg, fetch_metadata(identifier), collection, pdf_bytes=pdf_bytes)


def ingest_metadata(
    cfg: Config, meta: Metadata, collection: str | None = None, pdf_bytes: bytes | None = None
) -> tuple[str, Metadata]:
    """已知元数据时直接入库（供 search/collect 复用，避免重复抓元数据）。"""
    client = ConnectorClient(cfg.local_api.replace("/api", ""))
    if not client.ping():
        raise ConnectorError("Zotero 未运行，或未开启「允许其他应用与 Zotero 通信」")

    target = None
    if collection:
        target = client.resolve_target(collection)
        if target is None:
            names = [str(t.get("name")) for t in client.targets()]
            raise ConnectorError(f"找不到集合「{collection}」，可选：{names}")

    item = build_item(
        title=meta.title,
        authors=meta.authors,
        year=meta.year,
        publication=meta.publication,
        doi=meta.doi,
        url=meta.url,
        abstract=meta.abstract,
        item_type="journalArticle" if meta.publication and meta.publication != "arXiv" else "preprint",
        tags=["paperread-ingest"],
    )
    session, ids = client.save_items([item])
    if target:
        client.update_session(session, target=target)

    pdf = pdf_bytes or _download_pdf(meta, client)
    if pdf:
        client.save_attachment(session, ids[0], pdf, url=meta.url or meta.pdf_url, title="Full Text PDF")

    key = _wait_for_new_item(cfg, meta)
    if not key:
        raise ConnectorError(
            "已写入 Zotero，但没能在库里定位到新条目（可能尚未同步）。"
            "稍后跑 `paperread scan` 用标题/DOI 找到 key。"
        )
    return key, meta


def _wait_for_new_item(cfg: Config, meta: Metadata, timeout: float = 20.0) -> str | None:
    title_norm = _norm(meta.title)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            lib = load_library_auto(cfg)
        except Exception:  # noqa: BLE001
            time.sleep(1)
            continue
        for paper in lib.papers:
            if meta.doi and _norm(paper.doi) == _norm(meta.doi):
                return paper.key
            if title_norm and title_norm[:60] in _norm(paper.title):
                return paper.key
        time.sleep(1)
    return None


def pdf_path_of(paper: Paper) -> Path | None:
    att = paper.pdf
    return att.path if att and att.exists else None
