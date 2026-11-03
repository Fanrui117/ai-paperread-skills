"""笔记校验：链接 / 锚点 / 图片 / frontmatter / 引文可追溯性。

文件命名约定（v2）：
  <论文目录>/note.md      精读笔记
  <论文目录>/summary.md   一页速览
  <论文目录>/full.md      中英对照全文（^pN 锚点）
  <论文目录>/src.md       英文源
  <论文目录>/assets/      图片
链接统一用相对 markdown：[↓原文](full.md#^p7)、[PIPs](../PIPs-8VWR6W8W/note.md)
（兼容旧的 [[<简称>_fulltext#^p7|↓原文]] 写法）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from .config import Config
from .plan import load_state

FULLTEXT = "full.md"
SRC = "src.md"

WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
ANCHOR_RE = re.compile(r"\^(p\d+)")
QUOTE_RE = re.compile(r"原文[:：]\s*[“\"](.+?)[”\"]", re.S)
MD_NOISE_RE = re.compile(r"[*_`~<>]+")
WS_RE = re.compile(r"\s+")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "obsidian://")


@dataclass
class Issue:
    level: str
    msg: str


@dataclass
class VerifyResult:
    key: str
    short: str
    paper_dir: Path
    fulltext: Path | None = None
    anchors: set[str] = field(default_factory=set)
    notes: list[Path] = field(default_factory=list)
    links_total: int = 0
    links_ok: int = 0
    embeds_total: int = 0
    embeds_ok: int = 0
    quotes_total: int = 0
    quotes_ok: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _normalize(text: str) -> str:
    t = MD_NOISE_RE.sub(" ", text)
    t = t.replace("^", " ")
    return WS_RE.sub(" ", t).strip().lower()


def _split_target(inner: str) -> tuple[str, str]:
    """旧式 [[file#^p1|alias]] -> ("file", "p1")。"""
    inner = inner.split("|", 1)[0].strip()
    if "#" in inner:
        file_part, anchor = inner.split("#", 1)
        return file_part.strip(), anchor.strip().lstrip("^").strip()
    return inner, ""


def _md_targets(body: str):
    """相对 markdown 链接：[(text, target)]。"""
    for m in MD_LINK_RE.finditer(body):
        yield m.group(1), m.group(2).strip()


def verify_paper(cfg: Config, key: str) -> VerifyResult:
    state = load_state(cfg).get("papers", {}).get(key, {})
    if not state.get("dir"):
        raise RuntimeError(f"论文 {key} 还没有目录记录")
    short = state.get("short") or key
    paper_dir = cfg.library_root / state["dir"]
    res = VerifyResult(key=key, short=short, paper_dir=paper_dir)

    fulltext = paper_dir / FULLTEXT
    if not fulltext.is_file():
        legacy = next(iter(sorted(paper_dir.glob("*_fulltext.md"))), None)
        if legacy is not None:
            fulltext = legacy
            res.issues.append(Issue("warn", f"全文仍是旧文件名 {legacy.name}，建议重跑 translate"))
        else:
            src = paper_dir / SRC
            if not src.is_file():
                src = next(iter(sorted(paper_dir.glob("*_fulltext.src.md"))), None) or src
            if src.is_file():
                res.issues.append(Issue("warn", "只有英文源（src.md），尚未翻译"))
            else:
                res.issues.append(Issue("error", f"缺少全文文件：{FULLTEXT}"))
                return res
    res.fulltext = fulltext
    text = fulltext.read_text(encoding="utf-8")
    res.anchors = set(ANCHOR_RE.findall(text))
    norm_fulltext = _normalize(text)

    # frontmatter 检查
    try:
        fm = frontmatter.load(fulltext).metadata
        for k in ("paper_key", "title"):
            if not fm.get(k):
                res.issues.append(Issue("warn", f"全文 frontmatter 缺少 {k}"))
    except Exception as exc:  # noqa: BLE001
        res.issues.append(Issue("warn", f"全文 frontmatter 解析失败：{exc}"))

    # 全文自身的图片引用必须存在
    for rel in MD_IMG_RE.findall(text) + [f"assets/{i.split('|')[0].strip().removeprefix('assets/')}" for i in EMBED_RE.findall(text)]:
        rel = rel.strip()
        if rel.startswith(SKIP_SCHEMES):
            continue
        if not (paper_dir / rel).is_file():
            res.issues.append(Issue("error", f"{fulltext.name}: 图片不存在 ![]({rel})"))

    notes = [
        p
        for p in sorted(paper_dir.glob("*.md"))
        if p.name not in (fulltext.name, SRC, "src.md") and not p.name.endswith("_fulltext.src.md")
    ]
    res.notes = notes
    if not notes:
        res.issues.append(Issue("warn", "论文目录里还没有笔记文件"))

    for note in notes:
        try:
            nfm = frontmatter.load(note).metadata
            if not nfm.get("paper_key"):
                res.issues.append(Issue("warn", f"{note.name}: frontmatter 缺少 paper_key"))
            elif str(nfm.get("paper_key")) != key:
                res.issues.append(
                    Issue("error", f"{note.name}: frontmatter paper_key={nfm.get('paper_key')} 与论文 {key} 不一致")
                )
        except Exception as exc:  # noqa: BLE001
            res.issues.append(Issue("warn", f"{note.name}: frontmatter 解析失败：{exc}"))
        body = note.read_text(encoding="utf-8")

        # 1) 相对 markdown 链接（新格式）
        for _, target in _md_targets(body):
            if target.startswith(SKIP_SCHEMES):
                continue
            if target.startswith("#"):
                continue
            path_part, _, frag = target.partition("#")
            resolved = (note.parent / path_part).resolve() if path_part else note.resolve()
            res.links_total += 1
            if not resolved.is_file():
                res.issues.append(Issue("error", f"{note.name}: 链接目标不存在 ({target})"))
                continue
            if frag.startswith("^") and resolved == fulltext.resolve():
                if frag[1:] not in res.anchors:
                    res.issues.append(Issue("error", f"{note.name}: 锚点不存在 ({target})"))
                    continue
            res.links_ok += 1

        # 2) 旧式 wikilink（兼容）
        for inner in WIKILINK_RE.findall(body):
            file_part, anchor = _split_target(inner)
            if not file_part:
                continue
            res.links_total += 1
            target = paper_dir / (file_part if file_part.endswith(".md") else f"{file_part}.md")
            if not target.is_file():
                if any(cfg.library_root.rglob(f"{file_part}.md")):
                    res.links_ok += 1
                    continue
                res.issues.append(Issue("error", f"{note.name}: 链接目标不存在 [[{inner}]]"))
                continue
            if anchor and target.resolve() == fulltext.resolve() and anchor not in res.anchors:
                res.issues.append(Issue("error", f"{note.name}: 锚点不存在 [[{inner}]]"))
                continue
            res.links_ok += 1

        # 3) 图片：markdown 相对路径 + 旧式嵌入
        for rel in MD_IMG_RE.findall(body):
            rel = rel.strip()
            if rel.startswith(SKIP_SCHEMES):
                continue
            res.embeds_total += 1
            if (note.parent / rel).is_file():
                res.embeds_ok += 1
            else:
                res.issues.append(Issue("error", f"{note.name}: 图片不存在 ![]({rel})"))
        for inner in EMBED_RE.findall(body):
            path_part = inner.split("|", 1)[0].strip()
            res.embeds_total += 1
            if (paper_dir / path_part).is_file():
                res.embeds_ok += 1
            else:
                res.issues.append(Issue("error", f"{note.name}: 图片不存在 ![[{inner}]]"))

        # 4) 引文可追溯
        for quoted in QUOTE_RE.findall(body):
            res.quotes_total += 1
            q = _normalize(quoted)
            probe = q[:60] if len(q) > 60 else q
            if probe and probe in norm_fulltext:
                res.quotes_ok += 1
            else:
                res.issues.append(Issue("warn", f"{note.name}: 引文未在全文找到：{quoted[:60]}…"))

    # 未被引用的图片（提示，不算错）
    referenced = set()
    for note in notes:
        body = note.read_text(encoding="utf-8")
        for rel in MD_IMG_RE.findall(body):
            rel = rel.strip()
            if not rel.startswith(SKIP_SCHEMES):
                referenced.add((note.parent / rel).resolve())
        for inner in EMBED_RE.findall(body):
            referenced.add((paper_dir / inner.split("|", 1)[0].strip()).resolve())
    assets = [p for p in (paper_dir / "assets").glob("*.png")] if (paper_dir / "assets").is_dir() else []
    assets += [p for p in (paper_dir / "assets").glob("*.jpg")] if (paper_dir / "assets").is_dir() else []
    unused = [p for p in assets if p.resolve() not in referenced]
    if assets and len(unused) == len(assets):
        res.issues.append(Issue("warn", f"{len(assets)} 张图片均未被笔记引用"))
    return res


def render_report(res: VerifyResult) -> str:
    lines = [f"[verify] {res.short}-{res.key}"]
    if res.fulltext:
        lines.append(f"  fulltext: {res.fulltext.name}（{len(res.anchors)} 个锚点）")
    lines.append(f"  notes: {len(res.notes)}")
    lines.append(
        f"  链接: {res.links_ok}/{res.links_total}   图片: {res.embeds_ok}/{res.embeds_total}   引文: {res.quotes_ok}/{res.quotes_total}"
    )
    for issue in res.issues:
        mark = "ERROR" if issue.level == "error" else "WARN "
        lines.append(f"  [{mark}] {issue.msg}")
    lines.append("[OK] 校验通过" if res.ok else f"[FAIL] {len(res.errors)} 个错误")
    return "\n".join(lines)
