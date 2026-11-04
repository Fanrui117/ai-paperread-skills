"""写作包：把全文、大纲、图清单、元数据打包给 agent，供撰写精读笔记。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config
from .plan import load_json, load_state
from .translate import load_blocks


def build_packet(cfg: Config, key: str) -> Path:
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    short = data.get("short") or key
    state = load_state(cfg).get("papers", {}).get(key, {})
    paper_dir = cfg.library_root / state.get("dir", "")
    fulltext = paper_dir / "full.md"
    if not fulltext.is_file():
        fulltext = paper_dir / "src.md"

    lib = load_json(cfg.library_json, {}) or {}
    paper = next((p for p in lib.get("papers", []) if p.get("key") == key), {})
    if not paper.get("title") and fulltext.is_file() and fulltext.name == "full.md":
        # library 快照可能还没刷新：退回 full.md frontmatter 的元数据
        try:
            import frontmatter

            fm = frontmatter.load(fulltext).metadata
            paper = {
                "title": fm.get("title", ""),
                "creators": [c for c in str(fm.get("creators", "")).split("; ") if c],
                "date": str(fm.get("year", "")),
                "publication": fm.get("publication", ""),
                "url": fm.get("url", ""),
            }
        except Exception:  # noqa: BLE001
            pass

    lines: list[str] = []
    lines.append(f"# {short} 写作包")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Zotero key：`{key}`")
    lines.append(f"- 集合：{state.get('collection', '')}")
    lines.append(f"- 标题：{paper.get('title', '')}")
    lines.append(f"- 作者：{'; '.join((paper.get('creators') or [])[:12])}")
    lines.append(f"- 年份：{((paper.get('date') or '')[:4])}")
    lines.append(f"- 发表：{paper.get('publication', '')}")
    lines.append(f"- 链接：{paper.get('url', '')}")
    lines.append(f"- 全文：`{fulltext}`")
    lines.append(f"- 论文目录：`{paper_dir}`")
    lines.append("")

    lines.append("## 章节大纲（含块锚点）")
    lines.append("")
    last_heading = ""
    first_heading = True
    for idx, b in enumerate(blocks):
        if b["kind"] == "heading":
            plain = b["text"].lstrip("# ").replace("**", "").strip()
            if first_heading and plain.lower() == str(paper.get("title", "")).lower():
                first_heading = False
                continue
            first_heading = False
            last_heading = b["text"]
            lines.append(f"- {b['text']}")
        elif b["kind"] == "para" and last_heading:
            lines.append(f"  - 首段：`^{b['id']}`")
            last_heading = ""
    lines.append("")

    lines.append("## 图清单（含图注）")
    lines.append("")
    for idx, b in enumerate(blocks):
        if b["kind"] != "image":
            continue
        caption = ""
        for nxt in blocks[idx + 1 : idx + 3]:
            if nxt["kind"] == "caption":
                caption = nxt["text"].replace("\n", " ")[:220]
                break
        lines.append(f"- `{b['text']}`")
        if caption:
            lines.append(f"  - 图注：{caption}")
    lines.append("")

    abstract = (paper.get("abstract") or "").strip().replace("\n", " ")
    if abstract:
        lines.append("## 摘要（原文）")
        lines.append("")
        lines.append(abstract[:1200] + ("…" if len(abstract) > 1200 else ""))
        lines.append("")

    lines.append("## 表清单")
    lines.append("")
    for b in blocks:
        if b["kind"] == "table":
            first = b["text"].splitlines()[0][:100]
            lines.append(f"- `^{b['id']}`  {first}")
    lines.append("")

    lines.append("## 写作要求")
    lines.append("")
    lines.append("- 结构严格按 `templates/note-template.md`（Metadata 表 + 8 节）。")
    lines.append("- 每个论断/数字/结论附原文引用与 `[↓原文](full.md#^pN)` 相对链接。")
    lines.append("- 图片用 `![](assets/figN_xxx.png)` 相对路径，图注斜体写「该看什么」。")
    lines.append("- 公式用 Markdown 数学语法：行内 `$...$`、行间 `$$...$$`（不要用图片或 `_x_` 模拟上下标）。")
    lines.append("- 跨论文链接用 `[PaperB](../PaperB-YYYYYYYY/note.md)` 形式（相对路径）。")
    lines.append("- 不编造数字；原文没有的标注「原文未明确」。")
    lines.append("")

    out = cfg.state_dir / "packets" / f"{key}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
