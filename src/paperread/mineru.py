"""MinerU v4 云端解析 API 客户端（只做 PDF -> markdown + images，不做翻译）。

流程（与官方 v4 文档一致）：
  POST /file-urls/batch  -> {batch_id, file_urls:[presigned]}
  PUT  presigned URL     -> 上传 PDF
  GET  /extract-results/batch/{batch_id} -> 轮询直到 done
  GET  full_zip_url      -> 下载结果 zip（含 full.md、images/、content_list.json）
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Config

DEFAULT_BASE = "https://mineru.net/api/v4"
POLL_INTERVAL = 5
MAX_WAIT = 900


class MinerUError(RuntimeError):
    pass


@dataclass
class MinerUResult:
    batch_id: str
    markdown: str
    images: dict[str, bytes]
    content_list: list[dict] | None = None


def _client(cfg: Config, timeout: float = 120.0) -> httpx.Client:
    token = cfg.mineru_api_key
    if not token:
        raise MinerUError("未配置 MinerU API key：在 config/settings.toml 的 [mineru].api_key 填入")
    return httpx.Client(
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=timeout,
        transport=httpx.HTTPTransport(retries=2),  # 对连接层瞬断自动重试
        follow_redirects=True,
    )


def _json(resp: httpx.Response) -> dict:
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise MinerUError(f"MinerU 返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}") from exc
    if payload.get("code") != 0:
        raise MinerUError(f"MinerU API 错误 code={payload.get('code')} msg={payload.get('msg')}")
    return payload.get("data") or {}


def parse_pdf(cfg: Config, pdf_path: Path, *, model: str = "pipeline", timeout: int = MAX_WAIT) -> MinerUResult:
    base = (cfg.mineru_api_base or DEFAULT_BASE).rstrip("/")
    pdf_path = Path(pdf_path)
    body = {
        "files": [{"name": pdf_path.name, "data_id": pdf_path.stem[:60], "is_ocr": False}],
        "model_version": model,
        "enable_formula": True,
        "enable_table": True,
    }
    with _client(cfg) as client:
        data = _json(client.post(f"{base}/file-urls/batch", json=body))
        batch_id = data.get("batch_id")
        urls = data.get("file_urls") or []
        if not batch_id or not urls:
            raise MinerUError(f"MinerU 创建批次失败：{data}")
        upload_url = urls[0]
        with open(pdf_path, "rb") as f:
            payload = f.read()
        # 预签名 URL 对请求头敏感：不要加 Content-Type/Accept 之外的任何头
        up = client.put(upload_url, content=payload)
        if up.status_code not in (200, 201, 204):
            raise MinerUError(f"MinerU 上传失败 HTTP {up.status_code}: {up.text[:300]}")

        deadline = time.time() + timeout
        result_item: dict | None = None
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            info = _json(client.get(f"{base}/extract-results/batch/{batch_id}"))
            items = info.get("extract_result") or []
            if not items:
                continue
            item = items[0]
            state = item.get("state")
            if state == "done":
                result_item = item
                break
            if state in ("failed", "error"):
                raise MinerUError(f"MinerU 解析失败：{item.get('err_msg') or item}")
        if result_item is None:
            raise MinerUError(f"MinerU 解析超时（>{timeout}s）")

        zip_url = result_item.get("full_zip_url")
        if not zip_url:
            raise MinerUError(f"MinerU 结果缺少 full_zip_url：{result_item}")
        resp = client.get(zip_url, timeout=300)
        if resp.status_code != 200:
            raise MinerUError(f"下载结果 zip 失败 HTTP {resp.status_code}")
        zip_bytes = resp.content

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = zf.namelist()
    md_name = next((n for n in names if n.endswith("full.md")), None)
    if md_name is None:
        raise MinerUError(f"结果 zip 里没有 full.md（文件：{names[:10]}）")
    markdown = zf.read(md_name).decode("utf-8", "replace")
    images: dict[str, bytes] = {}
    for n in names:
        if n.startswith("images/") and not n.endswith("/"):
            images[Path(n).name] = zf.read(n)
    content_list = None
    cl_name = next((n for n in names if n.endswith("content_list.json")), None)
    if cl_name:
        try:
            content_list = json.loads(zf.read(cl_name).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            content_list = None
    return MinerUResult(batch_id=batch_id, markdown=markdown, images=images, content_list=content_list)
