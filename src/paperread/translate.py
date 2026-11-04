"""翻译与全文渲染：blocks.json -> <简称>_fulltext.md（中英交替 + ^pN 锚点）。

- provider：OpenAI 兼容 chat/completions（默认 DeepSeek）
- 缓存：vault/.paperread/work/<key>/translate_cache.json（按原文 sha256 命中，重跑不重复花钱）
- dry-run：不调用 API，只渲染英文 + 锚点，供先写笔记/先验收结构
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from .config import Config
from .plan import load_json, load_state, save_json

TRANSLATABLE = {"para", "caption", "heading"}
BAIDU_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"
SYSTEM_PROMPT = (
    "你是专业的学术论文翻译，把英文学术文本译成准确、通顺的简体中文。要求：\n"
    "1) 保留数学符号、公式、LaTeX 记号、引用编号 [12]、图表编号（Figure 3 / Table 2）、"
    "模型与数据集专有名词的英文原词；\n"
    "2) 不增删信息、不解释、不总结、不合并段落；\n"
    "3) 逐条对应翻译，输出 JSON 对象：键为段落 id，值为中文译文，不要输出任何其他文字。"
)


@dataclass
class TranslateResult:
    key: str
    short: str
    fulltext: Path
    mode: str
    blocks_total: int = 0
    translated: int = 0
    chars_in: int = 0
    chars_out: int = 0
    figures: int = 0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_blocks(cfg: Config, key: str) -> dict:
    path = cfg.state_dir / "work" / key / "blocks.json"
    data = load_json(path, None)
    if not data:
        raise RuntimeError(f"找不到块清单 {path}，请先 `paperread extract {key}`")
    return data


def load_glossary(cfg: Config) -> list[tuple[str, str]]:
    path = cfg.glossary_path
    if not path or not path.is_file():
        return []
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+|\s{2,}", line, maxsplit=1)
        if len(parts) == 2:
            pairs.append((parts[0].strip(), parts[1].strip()))
    return pairs


def _meta(cfg: Config, key: str, short: str) -> dict:
    lib = load_json(cfg.library_json, {}) or {}
    paper = next((p for p in lib.get("papers", []) if p.get("key") == key), None)
    state = load_state(cfg).get("papers", {}).get(key, {})
    out = {
        "paper_key": key,
        "short": short,
        "title": (paper or {}).get("title", ""),
        "collection": state.get("collection", ""),
        "creators": "; ".join((paper or {}).get("creators", [])[:12]),
        "year": ((paper or {}).get("date") or "")[:4],
        "publication": (paper or {}).get("publication", ""),
        "url": (paper or {}).get("url", ""),
        "pdf_sha256": state.get("pdf_sha256", ""),
        "pdf": next(
            (a.get("path") for a in (paper or {}).get("attachments", []) if a.get("content_type") == "application/pdf"),
            "",
        ),
    }
    return out


def _batches(items: list[tuple[str, str]], limit: int) -> list[list[tuple[str, str]]]:
    batches: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    size = 0
    for bid, text in items:
        n = len(text)
        if cur and size + n > limit:
            batches.append(cur)
            cur, size = [], 0
        cur.append((bid, text))
        size += n
    if cur:
        batches.append(cur)
    return batches


def _call_baidu(cfg: Config, batch: list[tuple[str, str]], glossary: list[tuple[str, str]]) -> dict[str, str]:
    """百度通用文本翻译（标准版 1 QPS，5 万字符/月免费额度，超出按量计费）。"""
    if not cfg.baidu_appid or not cfg.baidu_secret:
        raise RuntimeError("未配置百度翻译：在 config/settings.toml 填 [translate].baidu_appid / baidu_secret")
    texts = [re.sub(r"\s*\n\s*", " ", t).strip() for _, t in batch]
    q = "\n".join(texts)
    salt = str(int(time.time() * 1000))
    sign = hashlib.md5((cfg.baidu_appid + q + salt + cfg.baidu_secret).encode("utf-8")).hexdigest()
    payload = {"q": q, "from": "en", "to": "zh", "appid": cfg.baidu_appid, "salt": salt, "sign": sign}
    with httpx.Client(timeout=60) as client:
        resp = client.post(BAIDU_URL, data=payload)
    data = resp.json()
    if "error_code" in data:
        raise RuntimeError(f"百度翻译错误 {data.get('error_code')}: {data.get('error_msg')}")
    results = data.get("trans_result") or []
    out: dict[str, str] = {}
    if len(results) == len(batch):
        for (bid, _), item in zip(batch, results):
            out[bid] = str(item.get("dst", "")).strip()
    else:
        # 行数对不上（原文含换行等）：逐条重译，保证顺序正确
        for bid, text in batch:
            time.sleep(1.05)
            out.update(_call_baidu(cfg, [(bid, text)], glossary))
    return out


def _call_provider(cfg: Config, batch: list[tuple[str, str]], glossary: list[tuple[str, str]]) -> dict[str, str]:
    if cfg.translate_provider == "baidu":
        return _call_baidu(cfg, batch, glossary)
    return _call_deepseek(cfg, batch, glossary)


def _call_deepseek(cfg: Config, batch: list[tuple[str, str]], glossary: list[tuple[str, str]]) -> dict[str, str]:
    key = cfg.api_key()
    if not key:
        raise RuntimeError(
            "未配置翻译 API key：在 config/settings.toml 的 [translate].api_key 填入，"
            "或设置环境变量 DEEPSEEK_API_KEY；也可先用 --dry-run 只渲染英文"
        )
    glossary_text = ""
    if glossary:
        glossary_text = "术语表（必须按此译法）：\n" + "\n".join(f"- {en} => {zh}" for en, zh in glossary) + "\n\n"
    user = glossary_text + "请翻译以下段落：\n" + json.dumps({bid: text for bid, text in batch}, ensure_ascii=False)
    payload = {
        "model": cfg.translate_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    url = cfg.translate_api_base.rstrip("/") + "/chat/completions"
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with httpx.Client(timeout=180) as client:
                resp = client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"翻译接口返回 {resp.status_code}: {resp.text[:200]}")
                time.sleep([3, 8, 20, 30][attempt])
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"翻译接口返回 {resp.status_code}: {resp.text[:300]}")
            content = resp.json()["choices"][0]["message"]["content"]
            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"翻译接口返回的不是 JSON：{content[:300]}") from exc
            return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            last_error = exc
            time.sleep([3, 8, 20, 30][attempt])
    raise RuntimeError(f"翻译接口多次失败：{last_error}")


def _select_items(blocks: list[dict], main_only: bool = False) -> list[tuple[str, str]]:
    """挑出值得翻译的块：跳过封面/作者/参考文献/公式碎片，省 token 且少出错。"""
    items: list[tuple[str, str]] = []
    in_front = True  # Abstract 之前视为封面/作者信息
    in_refs = False
    for b in blocks:
        kind = b["kind"]
        text = (b.get("text") or "").strip()
        if kind == "heading":
            plain = text.lstrip("# ").replace("**", "").strip().lower()
            if plain.startswith("abstract") or plain.startswith("1 ") or plain.startswith("1."):
                in_front = False
            if plain == "references" or plain.startswith("references"):
                in_refs = True
                if main_only:
                    break
            elif in_refs and text.startswith("#"):
                # 参考文献之后的一级标题（如 A Appendix）恢复翻译
                level = len(text) - len(text.lstrip("#"))
                if level <= 2 and not re.match(r"^[#\s]*(references|bibliography)", plain):
                    in_refs = False
            if in_front or in_refs:
                continue
            items.append((b["id"], text))
            continue
        if in_front or in_refs:
            continue
        if kind not in ("para", "caption"):
            continue
        if len(text) < 20:
            continue
        alpha = sum(ch.isalpha() for ch in text) / max(len(text), 1)
        if alpha < 0.5:
            continue
        items.append((b["id"], text))
    return items


def _post_replace(text: str, rules: dict[str, str]) -> str:
    for bad, good in rules.items():
        text = text.replace(bad, good)
    return text


def _cache_path(cfg: Config, key: str, provider: str | None = None) -> Path:
    provider = provider or cfg.translate_provider
    return cfg.state_dir / "work" / key / f"translate_cache_{provider}.json"


def pick_cached_provider(cfg: Config, key: str) -> str:
    """选择缓存条目最多的 provider（重渲染时避免把已有译文丢掉）。"""
    best, best_n = "agent", 0
    for provider in ("agent", "baidu", "deepseek"):
        cache = load_json(_cache_path(cfg, key, provider), {}) or {}
        if len(cache) > best_n:
            best, best_n = provider, len(cache)
    return best


def emit_translation_tasks(cfg: Config, key: str, *, main_only: bool = False, chunk: int = 40) -> list[Path]:
    """把待翻译块导出成任务文件（供 agent 逐块翻译，不调用任何翻译 API）。"""
    data = load_blocks(cfg, key)
    short = data.get("short") or key
    items = _select_items(data["blocks"], main_only=main_only)
    cache = load_json(_cache_path(cfg, key, "agent"), {}) or {}
    todo = [{"id": bid, "text": text} for bid, text in items if _sha(text) not in cache]
    out_dir = cfg.state_dir / "work" / key / "translate_tasks"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(0, len(todo), chunk):
        part = todo[i : i + chunk]
        path = out_dir / f"part{i // chunk + 1:02d}.json"
        save_json(path, {"key": key, "short": short, "blocks": part})
        paths.append(path)
    if not paths:
        path = out_dir / "done.json"
        save_json(path, {"key": key, "short": short, "blocks": []})
        paths.append(path)
    return paths


def import_translations(cfg: Config, key: str, json_path: Path) -> int:
    """合并 agent 翻译结果 {id: 中文} 到 agent 缓存，返回写入条数。"""
    data = load_blocks(cfg, key)
    payload = load_json(Path(json_path), None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"翻译结果文件格式不对（应为 {{block_id: 中文}}）：{json_path}")
    cache = load_json(_cache_path(cfg, key, "agent"), {}) or {}
    texts = {b["id"]: b["text"] for b in data["blocks"] if b.get("id")}
    added = 0
    for bid, zh in payload.items():
        text = texts.get(str(bid))
        if text and isinstance(zh, str) and zh.strip():
            cache[_sha(text)] = zh.strip()
            added += 1
    save_json(_cache_path(cfg, key, "agent"), cache)
    return added


def translate_blocks(
    cfg: Config,
    key: str,
    blocks: list[dict],
    glossary: list[tuple[str, str]],
    *,
    dry_run: bool = False,
    max_blocks: int | None = None,
    main_only: bool = False,
    cache_only: bool = False,
) -> tuple[dict[str, str], int, int]:
    cache_path = _cache_path(cfg, key)
    cache: dict[str, str] = load_json(cache_path, {}) or {}
    if dry_run:
        return {}, 0, 0

    items = _select_items(blocks, main_only=main_only)
    if max_blocks:
        items = items[:max_blocks]
    if cache_only:
        zh_map: dict[str, str] = {}
        for b in blocks:
            if b["kind"] in TRANSLATABLE and b.get("id"):
                zh_map[b["id"]] = _post_replace(cache.get(_sha(b["text"]), ""), cfg.post_replace)
        return zh_map, 0, 0

    todo = [(bid, text) for bid, text in items if _sha(text) not in cache]
    translated = 0
    chars_in = 0
    chars_out = 0
    # 百度单次 q 上限 6000 字节；DeepSeek 可用更大批次
    batch_limit = cfg.translate_batch_chars if cfg.translate_provider == "baidu" else max(cfg.translate_batch_chars, 8000)
    for idx, batch in enumerate(_batches(todo, batch_limit)):
        if cfg.translate_provider == "baidu" and idx > 0:
            time.sleep(1.05)  # 百度标准版 1 QPS
        try:
            result = _call_provider(cfg, batch, glossary)
        except RuntimeError as exc:
            if "54003" in str(exc) or "limit" in str(exc).lower():
                time.sleep(3)
                result = _call_provider(cfg, batch, glossary)
            else:
                raise
        for bid, text in batch:
            zh = result.get(bid)
            if not zh:
                continue
            zh = _post_replace(zh, cfg.post_replace)
            cache[_sha(text)] = zh
            translated += 1
            chars_in += len(text)
            chars_out += len(zh)
        save_json(cache_path, cache)

    # 汇总：按当前 blocks 顺序取译文
    zh_map: dict[str, str] = {}
    for b in blocks:
        if b["kind"] in TRANSLATABLE and b.get("id"):
            zh_map[b["id"]] = _post_replace(cache.get(_sha(b["text"]), ""), cfg.post_replace)
    return zh_map, chars_in, chars_out


WIKI_IMAGE_RE = re.compile(r"!\[\[assets/([^\]\|]+)(?:\|[^\]]*)?\]\]")


def _to_md_image(text: str) -> str:
    """Obsidian 里 `![[assets/x]]` 是 vault 根相对路径，笔记在子目录时会找不到；
    统一改成相对 markdown 图片 `![](assets/x)`（与用户样例笔记一致）。"""
    return WIKI_IMAGE_RE.sub(lambda m: f"![](assets/{m.group(1)})", text)


def render_src(blocks: list[dict]) -> str:
    """从 blocks 重新生成英文源 src.md（保持与 full.md 同源）。"""
    parts: list[str] = []
    for b in blocks:
        kind = b.get("kind", "")
        text = b.get("text", "")
        if kind == "image":
            text = _to_md_image(text)
        parts.append(text)
    return "\n\n".join(parts).rstrip() + "\n"


def render_fulltext(cfg: Config, key: str, short: str, blocks: list[dict], zh: dict[str, str], meta: dict) -> str:
    lines: list[str] = []
    title = str(meta.get("title") or short)
    lines.append("---")
    for k in ("paper_key", "short", "title", "collection", "year", "publication", "url", "pdf_sha256"):
        v = str(meta.get(k, "") or "").replace('"', "'")
        if v:
            lines.append(f'{k}: "{v}"')
    lines.append(f'translation: "{cfg.translate_provider}:{cfg.translate_model if cfg.translate_provider == "deepseek" else "baidu"}"' if zh else 'translation: "none"')
    lines.append(f'translated_at: "{datetime.now().isoformat(timespec="seconds")}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    seen_h1 = False
    for b in blocks:
        kind = b["kind"]
        text = b.get("text", "")
        bid = b.get("id", "")
        if kind == "pagebreak":
            lines.append(text)
            lines.append("")
            continue
        if kind == "equation":
            lines.append(text)
            if bid:
                lines.append(f"^{bid}")
            lines.append("")
            continue
        if kind == "image":
            lines.append(_to_md_image(text))
            lines.append("")
            continue
        if kind == "table":
            lines.append(_to_md_image(text))
            if bid:
                lines.append(f"^{bid}")
            lines.append("")
            continue
        if kind == "heading":
            # 跳过与 frontmatter 标题重复的 PDF 首页大标题
            plain = text.lstrip("# ").replace("**", "").strip()
            if not seen_h1 and plain.lower() == title.lower():
                seen_h1 = True
                continue
            seen_h1 = True
            t = zh.get(bid) if bid else ""
            head = text
            if t:
                # agent 译文可能带 ## 前缀，去掉后再放进括号
                t = re.sub(r"^[#\s]+", "", t).replace("**", "").strip()
                head = f"{text}（{t}）"
            lines.append(head)
            lines.append("")
            continue
        # para / caption
        anchor = f" ^{bid}" if bid else ""
        lines.append(f"{text}{anchor}")
        lines.append("")
        t = zh.get(bid) if bid else ""
        if t:
            # 中文译文直接接在英文下面（不用 > 引用块，避免把后面的表格/代码块吞进 blockquote）
            lines.append(t)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def translate_paper(
    cfg: Config,
    key: str,
    *,
    dry_run: bool = False,
    max_blocks: int | None = None,
    provider: str | None = None,
    main_only: bool = False,
    cache_only: bool = False,
) -> TranslateResult:
    if provider:
        cfg.translate_provider = provider
    if cfg.translate_provider == "agent" and not (cache_only or dry_run):
        raise RuntimeError("provider=agent 不调用外部 API：请用 --emit-tasks 导出、--import-json 导入，或加 --dry-run")
    if cfg.translate_provider in ("baidu", "deepseek") and not (dry_run or cache_only):
        print(
            f"[translate] 使用 {cfg.translate_provider} API 翻译（会产生费用/消耗额度）；"
            f"想零外部 API 请用 --provider agent（导出任务给 agent）",
            file=sys.stderr,
        )
    data = load_blocks(cfg, key)
    blocks = data["blocks"]
    short = data.get("short") or key
    meta = _meta(cfg, key, short)
    glossary = load_glossary(cfg)

    zh, chars_in, chars_out = translate_blocks(
        cfg, key, blocks, glossary, dry_run=dry_run, max_blocks=max_blocks, main_only=main_only, cache_only=cache_only
    )
    md = render_fulltext(cfg, key, short, blocks, zh, meta)

    state = load_state(cfg)
    rec = state.get("papers", {}).get(key, {})
    paper_dir = cfg.library_root / rec.get("dir", "") if rec.get("dir") else cfg.library_root
    paper_dir.mkdir(parents=True, exist_ok=True)
    out = paper_dir / "full.md"
    out.write_text(md, encoding="utf-8")

    figures = sum(1 for b in blocks if b["kind"] == "image")
    return TranslateResult(
        key=key,
        short=short,
        fulltext=out,
        mode="dry-run" if dry_run else "api",
        blocks_total=len([b for b in blocks if b["kind"] != "pagebreak"]),
        translated=sum(1 for v in zh.values() if v),
        chars_in=chars_in,
        chars_out=chars_out,
        figures=figures,
    )
