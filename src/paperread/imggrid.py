"""图片排版（agent 辅助）：把「同一图的多个裁片」交给 agent 看页图后决定怎么排。

流程（与 formulas/tables 一致）：
  paperread imggrid <key> --emit-tasks
    -> work/<key>/imggrid_tasks.json
       [{group_id, block_ids, images, page, page_png, caption}]
  agent 看 page_png 后，为每组返回布局：
       {"<group_id>": {"mode": "raw",  "text": "<markdown 或 HTML>"}}
       {"<group_id>": {"mode": "grid"}}            # 强制并排（每行 3）
       {"<group_id>": {"mode": "stack"}}           # 竖排
  paperread imggrid <key> --import-json <file>
    -> 用一条 "figure" 块替换整组，重渲染 translated.md
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .plan import load_state, save_json
from .translate import _img_base, _img_name, load_blocks, pick_cached_provider, render_fulltext, translate_paper


@dataclass
class ImageGroup:
    gid: str
    blocks: list[dict]

    @property
    def images(self) -> list[str]:
        return [_img_name(b.get("text", "")) for b in self.blocks]

    @property
    def page(self) -> int:
        return int(self.blocks[0].get("page", 0) or 0)


def find_groups(blocks: list[dict]) -> list[ImageGroup]:
    groups: list[ImageGroup] = []
    i = 0
    while i < len(blocks):
        if blocks[i].get("kind") != "image":
            i += 1
            continue
        run = [blocks[i]]
        j = i + 1
        while j < len(blocks) and blocks[j].get("kind") == "image":
            run.append(blocks[j])
            j += 1
        # 按 base 切分
        cur: list[dict] = []
        for b in run:
            base = _img_base(_img_name(b.get("text", "")))
            if cur and _img_base(_img_name(cur[0].get("text", ""))) != base:
                if len(cur) > 1:
                    groups.append(ImageGroup(gid=f"g{len(groups) + 1}", blocks=cur))
                cur = []
            cur.append(b)
        if len(cur) > 1:
            groups.append(ImageGroup(gid=f"g{len(groups) + 1}", blocks=cur))
        i = j
    return groups


def _paper_dir(cfg: Config, key: str) -> Path:
    rec = load_state(cfg).get("papers", {}).get(key, {})
    if not rec.get("dir"):
        raise RuntimeError(f"{key} 没有目录记录")
    return cfg.library_root / rec["dir"]


def _caption_after(blocks: list[dict], group: ImageGroup) -> str:
    try:
        idx = blocks.index(group.blocks[-1])
    except ValueError:
        return ""
    for b in blocks[idx + 1 : idx + 4]:
        if b.get("kind") == "caption":
            return b.get("text", "")
    return ""


def _page_png(cfg: Config, key: str, page: int) -> str:
    """渲染页图（供 agent 查看），返回相对路径字符串。"""
    import fitz

    from .tables import _pdf_path

    doc = fitz.open(_pdf_path(cfg, key))
    out_dir = cfg.state_dir / "work" / key / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"p{page:03d}.png"
    if not out.is_file():
        doc[page - 1].get_pixmap(dpi=150).save(out)
    return str(out)


def emit_tasks(cfg: Config, key: str) -> Path:
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    groups = find_groups(blocks)
    tasks = []
    for g in groups:
        tasks.append(
            {
                "group_id": g.gid,
                "block_ids": [b.get("id") for b in g.blocks],
                "images": g.images,
                "page": g.page,
                "page_png": _page_png(cfg, key, g.page) if g.page else "",
                "caption": _caption_after(blocks, g),
            }
        )
    out = cfg.state_dir / "work" / key / "imggrid_tasks.json"
    save_json(out, {"key": key, "short": data.get("short", key), "groups": tasks})
    return out


def _render_grid(images: list[str], per_row: int = 3) -> str:
    rows = [images[i : i + per_row] for i in range(0, len(images), per_row)]
    width = len(rows[0])
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for row in rows[1:]:
        cells = row + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def apply_layout(cfg: Config, key: str, json_path: Path) -> tuple[int, Path]:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"布局文件格式不对：{json_path}")
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    groups = find_groups(blocks)
    applied = 0
    for g in groups:
        spec = payload.get(g.gid)
        if not isinstance(spec, dict):
            continue
        mode = spec.get("mode", "grid")
        if mode == "raw":
            text = str(spec.get("text", "")).strip()
        elif mode == "grid":
            text = _render_grid([f"![](assets/{img})" for img in g.images])
        elif mode == "stack":
            text = "\n\n".join(f"![](assets/{img})" for img in g.images)
        else:
            continue
        if not text:
            continue
        first = g.blocks[0]
        first["kind"] = "figure"
        first["text"] = text
        for b in g.blocks[1:]:
            blocks.remove(b)
        applied += 1
    save_json(cfg.state_dir / "work" / key / "blocks.json", data)
    provider = pick_cached_provider(cfg, key)
    res = translate_paper(cfg, key, provider=provider, cache_only=True)
    return applied, res.fulltext
