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


def _table_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip().startswith("|")]


def table_looks_broken(text: str) -> bool:
    """检测被拍平的表头：短碎片（"Fi"/"rst"）或多行表头列数不一致。"""
    lines = _table_lines(text)
    if len(lines) < 2:
        return False
    header = [c.strip(" *") for c in lines[0].strip("|").split("|")]
    frags = [c for c in header if re.fullmatch(r"[A-Za-z]{1,3}", c)]
    counts = {len(ln.strip("|").split("|")) for ln in lines[1:]}
    return len(frags) >= 2 or len(counts) > 1


def emit_table_tasks(cfg: Config, key: str) -> Path:
    """导出疑似「表头被拍平」的表格任务（含页图），供 agent 按原页重写。"""
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    items = []
    for idx, b in enumerate(blocks):
        if b.get("kind") != "table" or not table_looks_broken(b.get("text", "")):
            continue
        caption = ""
        for nb in blocks[idx + 1 : idx + 3]:
            if nb.get("kind") == "caption":
                caption = nb.get("text", "")
                break
        page = int(b.get("page", 0) or 0)
        items.append(
            {
                "block_id": b.get("id"),
                "page": page,
                "caption": caption,
                "header": _table_lines(b.get("text", ""))[0],
                "table": b.get("text", ""),
                "page_png": _page_png(cfg, key, page) if page else "",
            }
        )
    out = cfg.state_dir / "work" / key / "table_fix_tasks.json"
    save_json(out, {"key": key, "short": data.get("short", key), "items": items})
    return out


def _page_png(cfg: Config, key: str, page: int) -> str:
    import fitz

    doc = fitz.open(_pdf_path(cfg, key))
    out_dir = cfg.state_dir / "work" / key / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"p{page:03d}.png"
    if not out.is_file():
        doc[page - 1].get_pixmap(dpi=150).save(out)
    return str(out)


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

    pdf = _pdf_path(cfg, key)
    st = pdf.stat()
    sig = f"{st.st_size}-{int(st.st_mtime)}"
    cache = cfg.state_dir / "work" / key / "mineru_tables.json"
    if cache.is_file():
        data = load_json(cache, {}) or {}
        if data.get("sig") == sig and data.get("v") == 2:
            return [(int(t["page"]), html_table_to_markdown(clean_table_html(t["html"]))) for t in data.get("tables", [])]

    res = parse_pdf(cfg, pdf)
    out: list[dict] = []
    for item in res.content_list:
        if item.get("type") != "table":
            continue
        body = item.get("table_body") or ""
        if not body.strip():
            continue
        out.append({"page": int(item.get("page_idx", 0)) + 1, "html": body})
    save_json(cache, {"v": 2, "sig": sig, "tables": out})
    return [(t["page"], html_table_to_markdown(clean_table_html(t["html"]))) for t in out]


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
