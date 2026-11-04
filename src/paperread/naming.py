"""论文简称与文件夹命名规则。"""
from __future__ import annotations

import re

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPLIT_RE = re.compile(r"\s*[:：]\s*|\s+[—–]\s+|\s+-\s+")


def sanitize(text: str, max_len: int = 80) -> str:
    """清理文件名非法字符，保留空格与中文。"""
    s = INVALID_CHARS.sub("-", text or "")
    s = re.sub(r"\s+", " ", s).strip(" .-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:max_len].strip(" .-")


def slugify(text: str, max_len: int = 60) -> str:
    """把简称压成无空格 slug，便于 wikilink 与文件名。"""
    s = sanitize(text, max_len=200)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s[:max_len].strip(" .-")


def guess_short(title: str, key: str, overrides: dict[str, str] | None = None) -> str:
    """从标题猜一个论文简称；overrides 支持按 item key 或完整标题覆写。"""
    overrides = overrides or {}
    for cand in (key, title):
        if cand in overrides:
            return slugify(overrides[cand], 40)
    t = (title or "").strip()
    if not t:
        return key
    head = SPLIT_RE.split(t, maxsplit=1)[0].strip()
    short = slugify(head, 40)
    return short or slugify(t, 40) or key


def folder_name(short: str, key: str) -> str:
    return f"{short}-{key}"


# 论文目录内的固定文件名（简称已在目录名里，文件名不再重复）
NOTE_FILE = "note.md"
SUMMARY_FILE = "summary.md"
FULLTEXT_FILE = "translated.md"  # 中英对照全文（旧名 full.md，保留兼容）
LEGACY_FULLTEXT = "full.md"
SRC_FILE = "src.md"
ASSETS_DIR = "assets"


def paper_files(short: str | None = None) -> dict[str, str]:
    return {
        "note": NOTE_FILE,
        "summary": SUMMARY_FILE,
        "fulltext": FULLTEXT_FILE,
        "src": SRC_FILE,
    }
