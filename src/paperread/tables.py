"""表格升级：用 MinerU 的 HTML 表格（保留 rowspan/colspan）替换 pymupdf4llm 的破损管道表格。

  paperread tables <key>                 -> MinerU 解析并升级所有表格（保留块 id / 锚点）
  paperread tables <key> --import-json f -> 手工/agent 提供 {"<block_id>": "<html 或 markdown 表格>"}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config
from .plan import load_json, load_state, save_json
from .tablemd import html_table_to_markdown
from .translate import load_blocks, pick_cached_provider, translate_paper

SPACE_RE = re.compile(r"\s+")


def _clean_math(m: re.Match) -> str:
    s = m.group(0)
    inner = s[1:-1]
    inner = SPACE_RE.sub(" ", inner).strip()
    # 去掉 LaTeX 里多余的括号内空格：{ a v g } -> {avg}
    inner = re.sub(r"\s*([{}_^])\s*", r"\1", inner)
    inner = re.sub(r"\s*\\mathrm\s*\{\s*", r"\\mathrm{", inner)
    inner = re.sub(
        r"\\(mathrm|text|operatorname)\{([^}]*)\}",
        lambda m: "\\" + m.group(1) + "{" + m.group(2).replace(" ", "") + "}",
        inner,
    )
    return "$" + inner + "$"


def clean_table_html(html: str) -> str:
    html = html.strip()
    html = re.sub(r"\s+", " ", html)
    html = re.sub(r"\s*>\s*", ">", html)
    # 单元格里的行内公式：压缩空格，避免 "$ { \delta _ { ... } } $" 这种
    html = re.sub(r"\$[^$]{1,400}\$", _clean_math, html)
    return html


def _pdf_path(cfg: Config, key: str) -> Path:
    lib = load_json(cfg.state_dir / "library.json", {}) or {}
    paper = next((p for p in lib.get("papers", []) if p.get("key") == key), None)
    if not paper:
        raise RuntimeError(f"library.json 里没有 {key}，先跑 scan")
    for att in paper.get("attachments", []):
        if att.get("content_type") == "application/pdf" and att.get("path"):
            return Path(att["path"])
    raise RuntimeError(f"{key} 没有 PDF 附件")


def _mineru_tables(cfg: Config, key: str) -> list[tuple[int, str]]:
    from .mineru import parse_pdf

    res = parse_pdf(cfg, _pdf_path(cfg, key))
    out: list[tuple[int, str]] = []
    for item in res.content_list:
        if item.get("type") != "table":
            continue
        body = item.get("table_body") or ""
        if not body.strip():
            continue
        md = html_table_to_markdown(clean_table_html(body))
        out.append((int(item.get("page_idx", 0)) + 1, md))
    return out


def upgrade_tables(cfg: Config, key: str) -> tuple[int, int, list[str]]:
    """返回 (替换数, 表格块总数, 警告列表)。"""
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    ours = [b for b in blocks if b.get("kind") == "table"]
    mineru = _mineru_tables(cfg, key)
    warnings: list[str] = []
    used: set[str] = set()
    replaced = 0
    for page, html in mineru:
        cands = [b for b in ours if b.get("page") == page and b["id"] not in used]
        if not cands:
            warnings.append(f"MinerU 第 {page} 页的表格在本篇没有对应块，已跳过")
            continue
        b = cands[0]
        b["text"] = html
        used.add(b["id"])
        replaced += 1
    for b in ours:
        if b["id"] not in used:
            warnings.append(f"块 {b['id']}（第 {b.get('page')} 页）没有匹配到 MinerU 表格，保留原样")
    save_json(cfg.state_dir / "work" / key / "blocks.json", data)
    provider = pick_cached_provider(cfg, key)
    translate_paper(cfg, key, provider=provider, cache_only=True)
    return replaced, len(ours), warnings


def import_tables(cfg: Config, key: str, json_path: Path) -> tuple[int, Path]:
    payload = load_json(Path(json_path), None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"表格结果格式不对（应为 {{block_id: 表格文本}}）：{json_path}")
    data = load_blocks(cfg, key)
    replaced = 0
    for b in data["blocks"]:
        if b.get("kind") != "table":
            continue
        new = payload.get(b.get("id"))
        if isinstance(new, str) and new.strip():
            b["text"] = new.strip()
            replaced += 1
    save_json(cfg.state_dir / "work" / key / "blocks.json", data)
    provider = pick_cached_provider(cfg, key)
    res = translate_paper(cfg, key, provider=provider, cache_only=True)
    return replaced, res.fulltext
