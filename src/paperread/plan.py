"""目录规划：把 Zotero 库映射成 Obsidian vault 的目录树，并做增量 diff。"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .naming import FULLTEXT_FILE, NOTE_FILE, SRC_FILE, SUMMARY_FILE, folder_name, guess_short, sanitize
from .zotero import Paper, ZoteroLibrary

FOLDER_RE = re.compile(r"^(?P<short>.+)-(?P<key>[A-Z0-9]{8})$")

STATUS_LABEL = {
    "new": "[new]",
    "exists": "[ok]",
    "stale": "[stale]",
    "orphan": "[orphan]",
    "no_pdf": "[nopdf]",
}
STATUS_ORDER = {"new": 0, "stale": 1, "exists": 2, "orphan": 3, "no_pdf": 4}


@dataclass
class PlanEntry:
    key: str
    title: str
    short: str
    folder: str
    collection: str
    target: Path
    status: str
    detail: str = ""
    pdf: Path | None = None
    pdf_sha256: str = ""
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "short": self.short,
            "folder": self.folder,
            "collection": self.collection,
            "target": str(self.target),
            "status": self.status,
            "detail": self.detail,
            "pdf": str(self.pdf) if self.pdf else None,
            "pdf_sha256": self.pdf_sha256,
            "files": self.files,
        }


@dataclass
class Plan:
    entries: list[PlanEntry] = field(default_factory=list)
    generated_at: str = ""

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.status] = out.get(e.status, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {"generated_at": self.generated_at, "entries": [e.to_dict() for e in self.entries]}


# ---------- 小工具 ----------

def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------- 状态 ----------

def load_state(cfg: Config) -> dict:
    state = load_json(cfg.state_json, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("papers", {})
    return state


def _pdf_sha(paper: Paper, state: dict) -> str:
    pdf = paper.pdf
    if pdf is None or pdf.path is None or not pdf.path.is_file():
        return ""
    cached = state.get("papers", {}).get(paper.key, {})
    if (
        cached.get("pdf_size") == pdf.size
        and abs(float(cached.get("pdf_mtime") or 0) - pdf.mtime) < 0.001
        and cached.get("pdf_sha256")
    ):
        return str(cached["pdf_sha256"])
    return sha256_file(pdf.path)


# ---------- 规划 ----------

def _collection_dir(cfg: Config, paper: Paper, lib: ZoteroLibrary) -> tuple[Path, str]:
    col = lib.main_collection(paper)
    if col is None:
        return cfg.library_root, "(未归集)"
    parts = [sanitize(p) for p in col.path if sanitize(p)]
    return cfg.library_root.joinpath(*parts), "/".join(col.path)


def _is_ignored(paper: Paper, lib: ZoteroLibrary, cfg: Config) -> bool:
    """命中 [ignore] 的条目/集合则跳过（Zdraft 草稿、已删 PDF 的孤儿等）。"""
    if paper.key in set(cfg.ignore_items):
        return True
    cols = lib.collections_of(paper)
    if cols and all(any(part in cfg.ignore_collections for part in c.path) for c in cols):
        return True
    return False


def build_plan(lib: ZoteroLibrary, cfg: Config, state: dict | None = None) -> Plan:
    state = state if state is not None else load_state(cfg)
    plan = Plan(generated_at=datetime.now().isoformat(timespec="seconds"))
    known_keys = {p.key for p in lib.papers}

    for paper in lib.papers:
        if _is_ignored(paper, lib, cfg):
            continue
        short = guess_short(paper.title, paper.key, cfg.names)
        folder = folder_name(short, paper.key)
        col_dir, col_name = _collection_dir(cfg, paper, lib)
        target = col_dir / folder
        pdf = paper.pdf
        sha = _pdf_sha(paper, state) if pdf else ""

        if pdf is None:
            status, detail = "no_pdf", "无 PDF 附件" + ("" if paper.collection_ids else "（且未归集）")
        elif not pdf.exists:
            status, detail = "no_pdf", f"PDF 文件缺失：{pdf.path}" + ("" if paper.collection_ids else "（且未归集）")
        elif target.is_dir():
            prev = state.get("papers", {}).get(paper.key, {}).get("pdf_sha256", "")
            if prev and prev != sha:
                status, detail = "stale", "PDF 已更新，需重跑"
            else:
                status, detail = "exists", "目录已存在" + ("" if prev else "（无状态记录）")
        else:
            status, detail = "new", "待创建"

        plan.entries.append(
            PlanEntry(
                key=paper.key,
                title=paper.title,
                short=short,
                folder=folder,
                collection=col_name,
                target=target,
                status=status,
                detail=detail,
                pdf=pdf.path if pdf else None,
                pdf_sha256=sha,
                files=[NOTE_FILE, SUMMARY_FILE, FULLTEXT_FILE, SRC_FILE, "assets/"],
            )
        )

    # 孤儿目录：vault 里存在但 Zotero 库中已没有的论文目录
    if cfg.library_root.is_dir():
        for d in sorted(cfg.library_root.rglob("*")):
            if not d.is_dir() or any(part.startswith(".") for part in d.relative_to(cfg.library_root).parts):
                continue
            m = FOLDER_RE.match(d.name)
            if not m:
                continue
            key = m.group("key")
            if key in known_keys:
                continue
            plan.entries.append(
                PlanEntry(
                    key=key,
                    title="(Zotero 库中不存在)",
                    short=m.group("short"),
                    folder=d.name,
                    collection=str(d.parent.relative_to(cfg.library_root)).replace("\\", "/"),
                    target=d,
                    status="orphan",
                    detail="目录存在但 Zotero 已无此条目（不会自动删除）",
                )
            )

    plan.entries.sort(key=_sort_key)
    return plan


def _sort_key(e: PlanEntry):
    # 未归集条目排在最后
    col_key = (1, "") if e.collection.startswith("(") else (0, e.collection)
    return (col_key, STATUS_ORDER.get(e.status, 9), e.folder)


def render_plan(plan: Plan, cfg: Config) -> str:
    lines: list[str] = []
    lines.append(f"vault: {cfg.library_root}")
    counts = plan.counts()
    summary = "  ".join(f"{k}={counts.get(k, 0)}" for k in ("new", "stale", "exists", "no_pdf", "orphan"))
    lines.append(f"共 {len(plan.entries)} 项：{summary}")
    lines.append("")

    current = None
    for e in plan.entries:
        if e.collection != current:
            current = e.collection
            lines.append(f"{current}/")
        lines.append(f"  {STATUS_LABEL.get(e.status, e.status):8s} {e.folder}")
        if e.status in ("new", "stale"):
            lines.append(f"           └ 将生成: {', '.join(e.files)}")
        elif e.status in ("no_pdf", "orphan"):
            lines.append(f"           └ {e.detail}")
    return "\n".join(lines)


def apply_plan(plan: Plan, cfg: Config, state: dict) -> list[Path]:
    """只创建目录并记录状态；不写任何笔记/全文文件。"""
    created: list[Path] = []
    for e in plan.entries:
        if e.status not in ("new", "exists", "stale"):
            continue
        if not e.target.is_dir():
            e.target.mkdir(parents=True, exist_ok=True)
            created.append(e.target)
        rec = state["papers"].setdefault(e.key, {})
        rec.update(
            {
                "short": e.short,
                "folder": e.folder,
                "dir": str(e.target.relative_to(cfg.library_root)).replace("\\", "/"),
                "collection": e.collection,
                "pdf_sha256": e.pdf_sha256,
                "pdf_size": e.pdf.stat().st_size if e.pdf and e.pdf.is_file() else 0,
                "pdf_mtime": e.pdf.stat().st_mtime if e.pdf and e.pdf.is_file() else 0,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(cfg.state_json, state)
    return created
