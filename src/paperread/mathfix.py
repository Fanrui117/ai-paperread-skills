"""行内公式修正：把中文译文里的数学片段改写成 `$...$`（保留锚点，只改译文）。

  paperread mathfix <key> --emit-tasks   -> work/<key>/mathfix_tasks/partNN.json [{id, source, zh}]
  agent 把 zh 里的数学片段包成 $...$，写 {"<block_id>": "<新中文>"}
  paperread mathfix <key> --import-json <f>  -> 更新 agent 翻译缓存并重渲染 full.md
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .plan import load_json, load_state, save_json
from .translate import (
    _cache_path,
    _post_replace,
    _sha,
    load_blocks,
    pick_cached_provider,
    render_src,
    translate_paper,
)

# 疑似数学片段：上下标、希腊字母、数学符号、Unicode 数学斜体、<sup>/<sub>、已有 $（跳过已修好的）
MATH_HINT = re.compile(
    r"[_^]\{?[A-Za-z0-9]|\$|[\u0370-\u03ff]|[\U0001D400-\U0001D7FF]|<sup>|<sub>"
    r"|[⊙∑√∈≤≥≈∼×·∥→∫∞∝≪≫≠±]|\\frac|\\sum"
)


def _zh_of(cfg: Config, blocks: list[dict], cache: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for b in blocks:
        if b.get("kind") in ("para", "caption") and b.get("id"):
            zh = cache.get(_sha(b["text"]), "")
            if zh:
                out[b["id"]] = _post_replace(zh, cfg.post_replace)
    return out


def emit_mathfix_tasks(cfg: Config, key: str, *, chunk: int = 30) -> list[Path]:
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    cache = load_json(_cache_path(cfg, key, "agent"), {}) or {}
    zh_map = _zh_of(cfg, blocks, cache)
    items = []
    for b in blocks:
        bid = b.get("id")
        zh = zh_map.get(bid, "")
        if not zh or "$" in zh:
            continue
        if MATH_HINT.search(zh):
            items.append({"id": bid, "source": b["text"], "zh": zh})
    out_dir = cfg.state_dir / "work" / key / "mathfix_tasks"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(0, len(items), chunk):
        path = out_dir / f"part{i // chunk + 1:02d}.json"
        save_json(path, {"key": key, "short": data.get("short", key), "items": items[i : i + chunk]})
        paths.append(path)
    if not paths:
        path = out_dir / "done.json"
        save_json(path, {"key": key, "short": data.get("short", key), "items": []})
        paths.append(path)
    return paths


def fix_source(cfg: Config, key: str, json_path: Path) -> tuple[int, Path]:
    """修正被 PDF 抽取破坏的源段落（同时更新译文缓存键，避免丢译文）。

    payload: {"<block_id>": {"src": "<修正后英文>", "zh": "<修正后中文（可选）>"}}
    """
    payload = load_json(Path(json_path), None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"格式不对（应为 {{block_id: {{src, zh}}}}）：{json_path}")
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    caches = {p: (load_json(_cache_path(cfg, key, p), {}) or {}) for p in ("agent", "baidu", "deepseek")}
    updated = 0
    for b in blocks:
        item = payload.get(b.get("id"))
        if not isinstance(item, dict):
            continue
        new_src = item.get("src")
        if not isinstance(new_src, str) or not new_src.strip():
            continue
        old_src = b["text"]
        new_zh = item.get("zh")
        for provider, cache in caches.items():
            old_hash = _sha(old_src)
            if old_hash not in cache:
                continue
            if isinstance(new_zh, str) and new_zh.strip():
                cache[_sha(new_src.strip())] = new_zh.strip()
            cache.pop(old_hash, None)
        b["text"] = new_src.strip()
        updated += 1
    save_json(cfg.state_dir / "work" / key / "blocks.json", data)
    for provider, cache in caches.items():
        save_json(_cache_path(cfg, key, provider), cache)
    provider = pick_cached_provider(cfg, key)
    res = translate_paper(cfg, key, provider=provider, cache_only=True)
    # 同步英文源 src.md（避免 fixsrc 后 src 与 full 不一致）
    rec = load_state(cfg).get("papers", {}).get(key, {})
    if rec.get("dir"):
        paper_dir = cfg.library_root / rec["dir"]
        if (paper_dir / "src.md").is_file():
            (paper_dir / "src.md").write_text(render_src(blocks), encoding="utf-8")
    return updated, res.fulltext


def import_mathfix(cfg: Config, key: str, json_path: Path) -> tuple[int, Path]:
    payload = load_json(Path(json_path), None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"结果格式不对（应为 {{block_id: 新中文}}）：{json_path}")
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    cache = load_json(_cache_path(cfg, key, "agent"), {}) or {}
    texts = {b["id"]: b["text"] for b in blocks if b.get("id")}
    updated = 0
    for bid, new_zh in payload.items():
        text = texts.get(str(bid))
        if text and isinstance(new_zh, str) and new_zh.strip():
            cache[_sha(text)] = new_zh.strip()
            updated += 1
    save_json(_cache_path(cfg, key, "agent"), cache)
    provider = pick_cached_provider(cfg, key)
    res = translate_paper(cfg, key, provider=provider, cache_only=True)
    return updated, res.fulltext
