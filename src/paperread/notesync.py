"""笔记同步：把 note.md / summary.md 里「已经被升级为 LaTeX 的公式图」替换成 $$...$$。

背景：笔记是早期写的，里面用 ![](assets/eq1_p5.png) 嵌入公式图；
后来 `paperread formulas` 把 full.md 里的公式图换成了 LaTeX，但笔记还指向图片。
本命令按 blocks.json 的 orig_image/latex 映射替换，其余图片（真图）保持不动。
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .plan import load_state
from .translate import load_blocks

EMBED_RE = re.compile(r"!\[\[assets/([^\]\|]+)(?:\|[^\]]*)?\]\]|!\[[^\]]*\]\(assets/([^)]+)\)")


def _mapping(cfg: Config, key: str) -> dict[str, str]:
    """image 文件名 -> latex 正文（blocks.orig_image 优先，回退 work 里的任务/结果文件）。"""
    out: dict[str, str] = {}
    try:
        data = load_blocks(cfg, key)
    except Exception:  # noqa: BLE001
        return out
    for b in data.get("blocks", []):
        if b.get("kind") != "equation":
            continue
        img = b.get("orig_image")
        latex = b.get("latex")
        if img and latex:
            out[img] = latex

    # 兼容早期数据：formula_tasks.json（id -> image）+ formula_latex*.json（id -> latex）
    work = cfg.state_dir / "work" / key
    tasks = work / "formula_tasks.json"
    if tasks.is_file():
        try:
            import json

            imgs = {
                it["id"]: it["image"].split("assets/")[-1]
                for it in json.loads(tasks.read_text(encoding="utf-8")).get("items", [])
            }
        except Exception:  # noqa: BLE001
            imgs = {}
        latex_by_id: dict[str, str] = {}
        for f in work.glob("formula_latex*.json"):
            try:
                latex_by_id.update(json.loads(f.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass
        for bid, img in imgs.items():
            if bid in latex_by_id and img not in out:
                out[img] = latex_by_id[bid]
    return out


def sync_notes(cfg: Config, key: str, *, apply: bool = True) -> tuple[int, int, list[str]]:
    """返回 (替换数, 保留的其它图片嵌入数, 改动文件列表)。"""
    state = load_state(cfg).get("papers", {}).get(key, {})
    if not state.get("dir"):
        raise RuntimeError(f"{key} 没有目录记录")
    paper_dir = cfg.library_root / state["dir"]
    mapping = _mapping(cfg, key)
    replaced = kept = 0
    touched: list[str] = []

    for name in ("note.md", "summary.md"):
        path = paper_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")

        def repl(m: re.Match) -> str:
            nonlocal replaced, kept
            img = m.group(1) or m.group(2) or ""
            img = img.strip().split("/")[-1]
            if img in mapping:
                replaced += 1
                return "$$\n" + mapping[img] + "\n$$"
            kept += 1
            return m.group(0)

        new = EMBED_RE.sub(repl, text)
        if new != text and apply:
            path.write_text(new, encoding="utf-8")
            touched.append(name)
    return replaced, kept, touched
