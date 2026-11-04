"""Zotero Connector 写入客户端（可选，用于把论文「入库」到 Zotero）。

只使用 Zotero 桌面版自带的 connector 接口（浏览器插件用的同一套协议），
不直接改 zotero.sqlite，因此比直接写库安全：

  GET  /connector/ping                  -> "Zotero is running"
  POST /connector/saveItems             -> 保存条目元数据
  POST /connector/updateSession         -> 把条目移动到指定集合（target 形如 "C123"）
  POST /connector/saveAttachment        -> 上传 PDF 附件（X-Metadata 头 + 原始字节）
  POST /connector/getSelectedCollection -> 当前目标与可选集合列表

限制（与浏览器插件一致）：只能写入个人库；不能更新/删除；返回值不含新条目 key，
需要稍后用本地 API / sqlite 反查。
"""
from __future__ import annotations

import json
import random
import string
from pathlib import Path

import httpx

DEFAULT_BASE = "http://127.0.0.1:23119"
CONNECTOR_HEADERS = {
    "Content-Type": "application/json",
    "x-zotero-connector-api-version": "3",
    "x-zotero-connector-version": "5.0.125",
}


def _rand(n: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


class ConnectorError(RuntimeError):
    pass


class ConnectorClient:
    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def ping(self) -> bool:
        try:
            r = httpx.get(f"{self.base}/connector/ping", timeout=3)
            return r.status_code == 200 and "zotero is running" in r.text.lower()
        except Exception:  # noqa: BLE001
            return False

    def targets(self) -> list[dict]:
        """可选保存位置：[{id: "L1"/"C123", name, level}]。"""
        try:
            r = httpx.post(f"{self.base}/connector/getSelectedCollection", headers=CONNECTOR_HEADERS, json={}, timeout=10)
            data = r.json() if r.content else {}
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"无法读取 Zotero 集合列表：{exc}") from exc
        return data.get("targets", []) if isinstance(data, dict) else []

    def resolve_target(self, name: str) -> str | None:
        """按集合名（精确或包含）找 connector target，如 "C123"。"""
        want = name.strip().lower()
        for t in self.targets():
            if str(t.get("name", "")).strip().lower() == want:
                return str(t.get("id"))
        for t in self.targets():
            if want in str(t.get("name", "")).strip().lower():
                return str(t.get("id"))
        return None

    def save_items(self, items: list[dict], uri: str | None = None) -> tuple[str, list[str]]:
        session_id = _rand()
        ids = [f"{session_id}-{i}" for i in range(len(items))]
        payload = {
            "uri": uri or f"paperread://ingest/{session_id}",
            "sessionID": session_id,
            "items": [{"id": ids[i], **it} for i, it in enumerate(items)],
        }
        r = httpx.post(f"{self.base}/connector/saveItems", headers=CONNECTOR_HEADERS, json=payload, timeout=self.timeout)
        if r.status_code == 409:  # session 冲突，重试一次
            return self.save_items(items, uri)
        if r.status_code not in (200, 201):
            raise ConnectorError(f"saveItems 失败（HTTP {r.status_code}）：{r.text[:300]}")
        return session_id, ids

    def update_session(self, session_id: str, target: str | None = None, tags: list[str] | None = None, note: str | None = None) -> None:
        body: dict = {"sessionID": session_id}
        if target:
            body["target"] = target
        if tags:
            body["tags"] = tags
        if note is not None:
            body["note"] = note
        r = httpx.post(f"{self.base}/connector/updateSession", headers=CONNECTOR_HEADERS, json=body, timeout=self.timeout)
        text = r.text[:300]
        if r.status_code not in (200, 201) or "COLLECTION_NOT_FOUND" in text or "SESSION_NOT_FOUND" in text:
            raise ConnectorError(f"updateSession 失败（HTTP {r.status_code}）：{text}")

    def save_attachment(
        self,
        session_id: str,
        parent_connector_id: str,
        pdf_bytes: bytes,
        url: str,
        title: str = "Full Text PDF",
        content_type: str = "application/pdf",
    ) -> None:
        meta = {"sessionID": session_id, "parentItemID": parent_connector_id, "url": url, "title": title}
        headers = {
            **CONNECTOR_HEADERS,
            "Content-Type": content_type,
            "X-Metadata": json.dumps(meta),
        }
        r = httpx.post(f"{self.base}/connector/saveAttachment", headers=headers, content=pdf_bytes, timeout=300)
        if r.status_code not in (200, 201) or "not editable" in r.text.lower():
            raise ConnectorError(f"saveAttachment 失败（HTTP {r.status_code}）：{r.text[:300]}")


def build_item(
    *,
    title: str,
    authors: list[str],
    year: str = "",
    publication: str = "",
    doi: str = "",
    url: str = "",
    abstract: str = "",
    item_type: str = "journalArticle",
    tags: list[str] | None = None,
    pdf_path: Path | None = None,
) -> dict:
    """构造 connector saveItems 的 item 结构。"""
    creators = []
    for name in authors:
        if "," in name:
            last, first = name.split(",", 1)
            creators.append({"firstName": first.strip(), "lastName": last.strip(), "creatorType": "author"})
        else:
            parts = name.split()
            creators.append(
                {
                    "firstName": " ".join(parts[:-1]),
                    "lastName": parts[-1] if parts else name,
                    "creatorType": "author",
                }
            )
    attachments = []
    if pdf_path is not None:
        attachments.append({"title": "Full Text PDF", "path": str(pdf_path), "mimeType": "application/pdf"})
    return {
        "itemType": item_type,
        "title": title,
        "creators": creators,
        "date": year,
        "publicationTitle": publication,
        "DOI": doi,
        "url": url,
        "abstractNote": abstract,
        "tags": [{"tag": t} for t in (tags or [])],
        "attachments": attachments,
        "notes": [],
    }
