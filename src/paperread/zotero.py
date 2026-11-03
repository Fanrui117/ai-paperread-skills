"""Zotero 只读访问：拷贝 sqlite（含 WAL/SHM）到临时目录后查询，绝不改动原库。

优先实现 sqlite 源；本地 HTTP API（需用户在 Zotero 设置里开启）留作后续备选。
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SKIP_ITEM_TYPES = {"attachment", "note", "annotation"}

# 需要读取的 itemData 字段
WANTED_FIELDS = (
    "title", "date", "publicationTitle", "proceedingsTitle", "bookTitle",
    "repository", "url", "DOI", "abstractNote", "shortTitle", "extra",
)


class ZoteroError(RuntimeError):
    pass


@dataclass
class Attachment:
    item_id: int
    key: str
    content_type: str
    path: Path | None
    size: int = 0
    mtime: float = 0.0

    @property
    def exists(self) -> bool:
        return bool(self.path and self.path.is_file())

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "key": self.key,
            "content_type": self.content_type,
            "path": str(self.path) if self.path else None,
            "size": self.size,
            "mtime": self.mtime,
            "exists": self.exists,
        }


@dataclass
class Collection:
    collection_id: int
    key: str
    name: str
    parent_id: int | None
    path: tuple[str, ...] = ()

    @property
    def display_path(self) -> str:
        return "/".join(self.path) if self.path else self.name

    def to_dict(self) -> dict:
        return {
            "collection_id": self.collection_id,
            "key": self.key,
            "name": self.name,
            "parent_id": self.parent_id,
            "path": list(self.path),
        }


@dataclass
class Paper:
    item_id: int
    key: str
    item_type: str
    title: str
    creators: list[str] = field(default_factory=list)
    date: str = ""
    publication: str = ""
    url: str = ""
    doi: str = ""
    abstract: str = ""
    extra: str = ""
    tags: list[str] = field(default_factory=list)
    collection_ids: list[int] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def pdf(self) -> Attachment | None:
        pdfs = [a for a in self.attachments if a.content_type == "application/pdf"]
        pdfs = [a for a in pdfs if a.path is not None]
        if not pdfs:
            return None
        # 多个 PDF 时取最大的（通常是正文）
        return max(pdfs, key=lambda a: a.size)

    @property
    def year(self) -> str:
        return (self.date or "")[:4]

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "key": self.key,
            "item_type": self.item_type,
            "title": self.title,
            "creators": self.creators,
            "date": self.date,
            "publication": self.publication,
            "url": self.url,
            "doi": self.doi,
            "abstract": self.abstract,
            "extra": self.extra,
            "tags": self.tags,
            "collection_ids": self.collection_ids,
            "attachments": [a.to_dict() for a in self.attachments],
        }


@dataclass
class ZoteroLibrary:
    data_dir: Path
    collections: dict[int, Collection] = field(default_factory=dict)
    papers: list[Paper] = field(default_factory=list)
    deleted_count: int = 0

    # ---- 查询辅助 ----
    def paper_by_key(self, key: str) -> Paper | None:
        return next((p for p in self.papers if p.key == key), None)

    def collections_of(self, paper: Paper) -> list[Collection]:
        return [self.collections[cid] for cid in paper.collection_ids if cid in self.collections]

    def main_collection(self, paper: Paper) -> Collection | None:
        cols = self.collections_of(paper)
        if not cols:
            return None
        return min(cols, key=lambda c: (len(c.path), c.path))

    def collections_with_papers(self) -> list[Collection]:
        used = {cid for p in self.papers for cid in p.collection_ids}
        return sorted(
            (c for cid, c in self.collections.items() if cid in used),
            key=lambda c: c.path,
        )

    def orphans(self) -> list[Paper]:
        """有 PDF 但不在任何集合里的条目。"""
        return [p for p in self.papers if p.pdf and not p.collection_ids]

    def papers_without_pdf(self) -> list[Paper]:
        return [p for p in self.papers if not p.pdf and p.collection_ids]

    def to_dict(self) -> dict:
        return {
            "data_dir": str(self.data_dir),
            "collections": [c.to_dict() for c in sorted(self.collections.values(), key=lambda c: c.path)],
            "papers": [p.to_dict() for p in self.papers],
            "deleted_count": self.deleted_count,
        }


def _copy_db(data_dir: Path) -> Path:
    src = data_dir / "zotero.sqlite"
    if not src.is_file():
        raise ZoteroError(f"找不到 Zotero 数据库：{src}（请检查 [zotero].data_dir）")
    tmp = Path(tempfile.mkdtemp(prefix="paperread_zotero_"))
    for name in ("zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-shm"):
        p = data_dir / name
        if p.is_file():
            shutil.copy2(p, tmp / name)
    return tmp / "zotero.sqlite"


def _table_names(cur: sqlite3.Cursor) -> set[str]:
    return {r[0] for r in cur.execute("select name from sqlite_master where type='table'")}


def _resolve_attachment_path(data_dir: Path, att_key: str, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    if raw_path.startswith("storage:"):
        filename = raw_path.split(":", 1)[1]
        return data_dir / "storage" / att_key / filename
    p = Path(raw_path)
    return p if p.is_absolute() else None


def resolve_collection_paths(lib: ZoteroLibrary) -> None:
    """按 parent 链计算每个集合的完整路径（供 sqlite / local API 两个数据源共用）。"""

    def resolve_path(cid: int, seen: set[int] | None = None) -> tuple[str, ...]:
        seen = seen or set()
        c = lib.collections[cid]
        if cid in seen or c.parent_id is None or c.parent_id not in lib.collections:
            return (c.name,)
        seen.add(cid)
        return resolve_path(c.parent_id, seen) + (c.name,)

    for cid, c in lib.collections.items():
        c.path = resolve_path(cid)


def load_library_auto(cfg) -> ZoteroLibrary:
    """优先本地 HTTP API（实时、排除回收站），失败回退 sqlite 只读副本。"""
    if getattr(cfg, "prefer_local_api", False):
        try:
            from .zotero_api import load_library_api

            return load_library_api(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Zotero 本地 API 不可用（{type(exc).__name__}: {exc}），回退 sqlite 只读副本")
    return load_library(cfg.zotero_data_dir)


def load_library(data_dir: Path) -> ZoteroLibrary:
    """读取 Zotero 库（只读副本）。"""
    data_dir = Path(data_dir)
    db_path = _copy_db(data_dir)
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    tables = _table_names(cur)
    for required in ("items", "collections", "collectionItems", "itemAttachments", "itemData", "itemDataValues", "fields"):
        if required not in tables:
            raise ZoteroError(f"Zotero 数据库缺少表 {required}，可能不是有效的 zotero.sqlite")

    lib = ZoteroLibrary(data_dir=data_dir)
    try:
        fields = {r["fieldID"]: r["fieldName"] for r in cur.execute("select fieldID, fieldName from fields")}
        types = {r["itemTypeID"]: r["typeName"] for r in cur.execute("select itemTypeID, typeName from itemTypes")}

        deleted: set[int] = set()
        if "deletedItems" in tables:
            deleted = {r["itemID"] for r in cur.execute("select itemID from deletedItems")}
        lib.deleted_count = len(deleted)

        # 集合（排除回收站里已删除的集合）
        deleted_cols: set[int] = set()
        if "deletedCollections" in tables:
            deleted_cols = {r["collectionID"] for r in cur.execute("select collectionID from deletedCollections")}
        for r in cur.execute("select collectionID, collectionName, parentCollectionID, key from collections"):
            if r["collectionID"] in deleted_cols:
                continue
            lib.collections[r["collectionID"]] = Collection(
                collection_id=r["collectionID"],
                key=r["key"],
                name=r["collectionName"],
                parent_id=r["parentCollectionID"],
            )
        resolve_collection_paths(lib)

        # 条目归属
        membership: dict[int, list[int]] = {}
        for r in cur.execute("select collectionID, itemID from collectionItems"):
            if r["collectionID"] not in lib.collections:
                continue
            membership.setdefault(r["itemID"], []).append(r["collectionID"])

        # 条目基础信息
        item_rows = list(
            cur.execute(
                "select itemID, key, itemTypeID from items "
                "where itemTypeID in (select itemTypeID from itemTypes where typeName not in "
                "('attachment','note','annotation'))"
            )
        )
        papers: dict[int, Paper] = {}
        for r in item_rows:
            if r["itemID"] in deleted:
                continue
            tname = types.get(r["itemTypeID"], "document")
            if tname in SKIP_ITEM_TYPES:
                continue
            papers[r["itemID"]] = Paper(
                item_id=r["itemID"],
                key=r["key"],
                item_type=tname,
                title="",
                collection_ids=sorted(membership.get(r["itemID"], [])),
            )

        # 字段值
        wanted = set(WANTED_FIELDS)
        for r in cur.execute(
            "select d.itemID, f.fieldName, v.value from itemData d "
            "join fields f on f.fieldID = d.fieldID "
            "join itemDataValues v on v.valueID = d.valueID"
        ):
            p = papers.get(r["itemID"])
            if p is None or r["fieldName"] not in wanted:
                continue
            val = r["value"] or ""
            name = r["fieldName"]
            if name == "title":
                p.title = val
            elif name == "date":
                p.date = val
            elif name in ("publicationTitle", "proceedingsTitle", "bookTitle", "repository"):
                if not p.publication:
                    p.publication = val
            elif name == "url":
                p.url = val
            elif name == "DOI":
                p.doi = val
            elif name == "abstractNote":
                p.abstract = val
            elif name == "extra":
                p.extra = val

        # 作者
        for r in cur.execute(
            "select ic.itemID, c.firstName, c.lastName, c.fieldMode from itemCreators ic "
            "join creators c on c.creatorID = ic.creatorID order by ic.itemID, ic.orderIndex"
        ):
            p = papers.get(r["itemID"])
            if p is None:
                continue
            if r["fieldMode"] == 1:
                p.creators.append((r["lastName"] or "").strip())
            else:
                last = (r["lastName"] or "").strip()
                first = (r["firstName"] or "").strip()
                p.creators.append(f"{last}, {first}" if first else last)

        # 标签
        if "itemTags" in tables and "tags" in tables:
            for r in cur.execute(
                "select it.itemID, t.name from itemTags it join tags t on t.tagID = it.tagID"
            ):
                p = papers.get(r["itemID"])
                if p is not None:
                    p.tags.append(r["name"])

        # 附件
        att_meta = {
            r["itemID"]: (r["key"], r["contentType"] or "", r["path"], r["parentItemID"])
            for r in cur.execute(
                "select a.itemID, i.key as key, a.contentType, a.path, a.parentItemID "
                "from itemAttachments a join items i on i.itemID = a.itemID"
            )
        }
        for aid, (akey, ctype, raw_path, parent) in att_meta.items():
            p = papers.get(parent)
            if p is None:
                continue
            path = _resolve_attachment_path(data_dir, akey, raw_path)
            size = path.stat().st_size if path and path.is_file() else 0
            mtime = path.stat().st_mtime if path and path.is_file() else 0.0
            p.attachments.append(
                Attachment(item_id=aid, key=akey, content_type=ctype, path=path, size=size, mtime=mtime)
            )

        lib.papers = sorted(
            (p for p in papers.values() if p.collection_ids or p.attachments),
            key=lambda p: (p.title or "").lower(),
        )
        return lib
    finally:
        con.close()
