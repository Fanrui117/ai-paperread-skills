"""Zotero 本地 HTTP API 数据源（Zotero 7+ 设置里开启「允许其他应用与 Zotero 通信」后可用）。

优点：只返回存活集合/条目（自动排除回收站与已删集合），附件直链文件路径；
缺点：需要 Zotero 正在运行。不可用时由 zotero.load_library_auto 回退 sqlite。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .config import Config
from .zotero import Attachment, Collection, Paper, ZoteroLibrary, ZoteroError, resolve_collection_paths

SKIP_TYPES = {"attachment", "note", "annotation"}
PAGE = 100


def _client(cfg: Config, timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.local_api.rstrip("/"),
        headers={"Zotero-API-Version": "3"},
        timeout=timeout,
    )


def ping(cfg: Config) -> bool:
    try:
        with _client(cfg, timeout=3) as c:
            return c.get("/users/0/collections", params={"limit": 1}).status_code == 200
    except Exception:
        return False


def _paged(c: httpx.Client, path: str) -> list[dict]:
    out: list[dict] = []
    start = 0
    while True:
        r = c.get(path, params={"limit": PAGE, "start": start})
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        start += PAGE
        if start > 20000:
            return out


def _file_url_to_path(href: str | None) -> Path | None:
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.scheme != "file":
        return None
    p = unquote(parsed.path)
    if p.startswith("/") and len(p) > 2 and p[2] == ":":
        p = p[1:]  # /D:/... -> D:/...
    return Path(p)


def _creator_name(c: dict) -> str:
    if c.get("name"):
        return str(c["name"]).strip()
    last = (c.get("lastName") or "").strip()
    first = (c.get("firstName") or "").strip()
    return f"{last}, {first}" if first else last


def load_library_api(cfg: Config) -> ZoteroLibrary:
    with _client(cfg) as c:
        cols_raw = _paged(c, "/users/0/collections")
        items_raw = _paged(c, "/users/0/items")

    lib = ZoteroLibrary(data_dir=Path(cfg.zotero_data_dir))
    key2cid: dict[str, int] = {}
    for idx, row in enumerate(sorted(cols_raw, key=lambda x: x["data"].get("name", "")), start=1):
        d = row["data"]
        lib.collections[idx] = Collection(
            collection_id=idx,
            key=d["key"],
            name=d.get("name", ""),
            parent_id=None,
        )
        key2cid[d["key"]] = idx
    for idx, row in enumerate(sorted(cols_raw, key=lambda x: x["data"].get("name", "")), start=1):
        parent_key = row["data"].get("parentCollection")
        if parent_key and parent_key in key2cid:
            lib.collections[idx].parent_id = key2cid[parent_key]
    resolve_collection_paths(lib)

    papers: dict[str, Paper] = {}
    attachments_by_parent: dict[str, list[Attachment]] = {}
    for row in items_raw:
        d = row["data"]
        itype = d.get("itemType", "")
        key = d.get("key") or row.get("key")
        if itype == "attachment":
            parent = d.get("parentItem")
            if not parent:
                continue
            path = _file_url_to_path((row.get("links") or {}).get("enclosure", {}).get("href"))
            size = path.stat().st_size if path and path.is_file() else 0
            mtime = path.stat().st_mtime if path and path.is_file() else 0.0
            attachments_by_parent.setdefault(parent, []).append(
                Attachment(
                    item_id=0,
                    key=key,
                    content_type=d.get("contentType") or "",
                    path=path,
                    size=size,
                    mtime=mtime,
                )
            )
            continue
        if itype in SKIP_TYPES:
            continue
        cids = sorted(key2cid[k] for k in d.get("collections", []) if k in key2cid)
        papers[key] = Paper(
            item_id=0,
            key=key,
            item_type=itype,
            title=d.get("title") or "",
            creators=[_creator_name(x) for x in d.get("creators", []) if _creator_name(x)],
            date=d.get("date") or "",
            publication=d.get("publicationTitle") or d.get("proceedingsTitle") or d.get("repository") or "",
            url=d.get("url") or "",
            doi=d.get("DOI") or "",
            abstract=d.get("abstractNote") or "",
            extra=d.get("extra") or "",
            tags=[t.get("tag", "") for t in d.get("tags", [])],
            collection_ids=cids,
        )

    # 附件可能出现在父条目之前，统一在收集完后再挂载
    for parent_key, atts in attachments_by_parent.items():
        if parent_key in papers:
            papers[parent_key].attachments = atts

    lib.papers = sorted(
        (p for p in papers.values() if p.collection_ids or p.attachments),
        key=lambda p: (p.title or "").lower(),
    )
    if not lib.collections:
        raise ZoteroError("本地 API 返回空集合列表")
    return lib
