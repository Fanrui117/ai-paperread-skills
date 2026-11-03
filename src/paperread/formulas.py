"""公式升级：把 assets/eq*.png 公式图片原地替换为 LaTeX（保留块 id 与锚点）。

工作流（与 agent 翻译一致，不依赖外部公式 OCR 服务）：
  paperread formulas <key> --emit-tasks
    -> work/<key>/formula_tasks.json  [{id, image, page}]
  agent 逐张 read_image 识别公式，写 {"<block_id>": "<latex>"}
  paperread formulas <key> --import-json <file>
    -> 更新 blocks.json（kind=equation, text=$$...$$），重渲染 full.md，并同步 src.md
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .plan import load_json, load_state, save_json
from .translate import load_blocks, pick_cached_provider, translate_paper

EQ_IMAGE_RE = re.compile(r"assets/(eq[^\]\)\s]+|img\d+\.(?:png|jpg|jpeg))")


@dataclass
class FormulaTask:
    id: str
    image: str
    page: int


def _eq_blocks(blocks: list[dict], paper_dir: Path | None = None) -> list[dict]:
    """候选公式块：文件名 eq*，或短宽条图（抽取器有时把公式裁成 fig*.png）。"""
    out = []
    for b in blocks:
        if b.get("kind") != "image":
            continue
        text = b.get("text", "")
        m = EQ_IMAGE_RE.search(text)
        if m:
            out.append(b)
            continue
        if paper_dir is None:
            continue
        name = m.group(0).split("assets/")[-1] if m else ""
        if not name:
            try:
                name = text.split("assets/")[-1].split("]]")[0].split(")")[0]
            except Exception:  # noqa: BLE001
                continue
        path = paper_dir / "assets" / name
        if not path.is_file():
            continue
        try:
            from PIL import Image

            w, h = Image.open(path).size
        except Exception:  # noqa: BLE001
            continue
        if h <= 200 and w / max(h, 1) >= 2.2:
            out.append(b)
    return out


def emit_formula_tasks(cfg: Config, key: str) -> Path:
    data = load_blocks(cfg, key)
    state = load_state(cfg).get("papers", {}).get(key, {})
    paper_dir = cfg.library_root / state["dir"] if state.get("dir") else None
    tasks = []
    for b in _eq_blocks(data["blocks"], paper_dir):
        m = EQ_IMAGE_RE.search(b["text"])
        name = m.group(0) if m else b["text"].split("assets/")[-1].split("]]")[0].split(")")[0]
        tasks.append({"id": b["id"], "image": name, "page": b.get("page", 0)})
    out = cfg.state_dir / "work" / key / "formula_tasks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    save_json(out, {"key": key, "short": data.get("short", key), "items": tasks})
    return out


def _latex_body(latex: str) -> str:
    s = latex.strip()
    s = re.sub(r"^\$\$?", "", s)
    s = re.sub(r"\$\$?$", "", s)
    return s.strip()


def import_formulas(cfg: Config, key: str, json_path: Path) -> tuple[int, Path]:
    """返回 (替换数量, 重渲染后的 full.md)。"""
    payload = load_json(Path(json_path), None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"公式结果格式不对（应为 {{block_id: latex}}）：{json_path}")
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    replaced = 0
    for b in blocks:
        if b.get("kind") != "image":
            continue
        latex = payload.get(b.get("id"))
        if not isinstance(latex, str) or not latex.strip():
            continue
        b["kind"] = "equation"
        b["text"] = "$$\n" + _latex_body(latex) + "\n$$"
        b["latex"] = _latex_body(latex)
        replaced += 1

    # 同步 src.md（英文源）里的图片引用
    state = load_state(cfg).get("papers", {}).get(key, {})
    paper_dir = cfg.library_root / state["dir"]
    src = paper_dir / "src.md"
    if src.is_file():
        text = src.read_text(encoding="utf-8")
        for b in blocks:
            if b.get("kind") != "equation":
                continue
            # 该块原来的图片名已不可考，这里按 latex 无法反推；src.md 保持原样即可
        src.write_text(text, encoding="utf-8")

    work = cfg.state_dir / "work" / key / "blocks.json"
    save_json(work, data)
    provider = pick_cached_provider(cfg, key)
    res = translate_paper(cfg, key, provider=provider, cache_only=True)
    return replaced, res.fulltext
