"""PaperRead CLI：scan / plan / status。"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import load_config
from .naming import folder_name, guess_short
from .plan import (
    apply_plan,
    build_plan,
    load_json,
    load_state,
    render_plan,
    save_json,
)
from .zotero import ZoteroLibrary, load_library, load_library_auto


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _fmt_size(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f} KB"
    return f"{n} B"


def _scan_stats(lib: ZoteroLibrary) -> dict:
    with_pdf = [p for p in lib.papers if p.pdf]
    usable = [p for p in with_pdf if p.pdf and p.pdf.exists]
    in_col = [p for p in lib.papers if p.collection_ids]
    return {
        "collections": len(lib.collections),
        "collections_with_papers": len(lib.collections_with_papers()),
        "papers": len(lib.papers),
        "papers_with_pdf": len(with_pdf),
        "papers_with_pdf_file": len(usable),
        "papers_in_collections": len(in_col),
        "orphans": len(lib.orphans()),
        "papers_without_pdf": len(lib.papers_without_pdf()),
        "deleted": lib.deleted_count,
    }


def cmd_scan(cfg, args) -> int:
    lib = load_library_auto(cfg)
    cfg.ensure_dirs()
    payload = lib.to_dict()
    payload["scanned_at"] = datetime.now().isoformat(timespec="seconds")
    payload["stats"] = _scan_stats(lib)
    save_json(cfg.library_json, payload)

    if args.json:
        import json

        print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))
        print(f"已写入 {cfg.library_json}")
        return 0

    st = payload["stats"]
    print(f"Zotero 数据目录: {lib.data_dir}")
    print(
        f"集合 {st['collections']} 个（含论文 {st['collections_with_papers']} 个）  "
        f"论文 {st['papers']} 篇（有 PDF 记录 {st['papers_with_pdf']}，文件可用 {st['papers_with_pdf_file']}，"
        f"在集合内 {st['papers_in_collections']}，孤儿 {st['orphans']}）  回收站 {st['deleted']}"
    )
    print(f"已写入: {cfg.library_json}\n")

    width = max((len(folder_name(guess_short(p.title, p.key, cfg.names), p.key)) for p in lib.papers), default=28)
    print("集合树:")
    for col in lib.collections_with_papers():
        print(f"  {col.display_path}/")
        for p in lib.papers:
            if col.collection_id not in p.collection_ids:
                continue
            short = guess_short(p.title, p.key, cfg.names)
            folder = folder_name(short, p.key)
            pdf = p.pdf
            if pdf and pdf.exists:
                pdf_info = f"pdf {_fmt_size(pdf.size)}"
            elif pdf:
                pdf_info = "PDF 缺失"
            else:
                pdf_info = "无 PDF"
            print(f"    - {folder:{width}s} {p.title[:64]:64s} [{pdf_info}]")

    orphans = lib.orphans()
    if orphans:
        print("\n孤儿（有 PDF 记录，但未归入任何集合）:")
        for p in orphans:
            pdf = p.pdf
            mark = f"pdf {_fmt_size(pdf.size)}" if pdf and pdf.exists else "PDF 文件缺失"
            print(f"  - {p.key}  [{mark}]  {p.title[:70]}")

    no_pdf = lib.papers_without_pdf()
    if no_pdf:
        print("\n无 PDF（在集合内，暂不处理）:")
        for p in no_pdf:
            cols = ", ".join(c.display_path for c in lib.collections_of(p))
            print(f"  - {p.key}  {p.title[:70]}  ({cols})")
    return 0


def cmd_plan(cfg, args) -> int:
    lib = load_library_auto(cfg)
    state = load_state(cfg)
    plan = build_plan(lib, cfg, state)

    if args.json:
        import json

        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_plan(plan, cfg))

    save_json(cfg.plan_json, plan.to_dict())

    if args.apply:
        created = apply_plan(plan, cfg, state)
        print(f"\n已创建 {len(created)} 个目录，状态写入 {cfg.state_json}")
    else:
        print(f"\n（预览模式，未改动 vault；确认后加 --apply 创建目录）")
    return 0


def cmd_status(cfg, args) -> int:
    from .plan import _is_ignored

    lib = load_library_auto(cfg)
    state = load_state(cfg)
    recorded = state.get("papers", {})

    usable = [p for p in lib.papers if p.pdf and p.pdf.exists and not _is_ignored(p, lib, cfg)]
    print(f"vault: {cfg.library_root}")
    print(f"数据源: Zotero   可用论文: {len(usable)}   已登记目录: {len(recorded)}\n")

    groups: dict[str, list] = {}
    for paper in usable:
        col = lib.main_collection(paper)
        groups.setdefault(col.display_path if col else "(未归集)", []).append(paper)

    for col in sorted(groups):
        print(f"{col}/")
        for paper in sorted(groups[col], key=lambda p: p.title.lower()):
            rec = recorded.get(paper.key) or {}
            short = rec.get("short") or guess_short(paper.title, paper.key, cfg.names)
            folder = rec.get("dir", "").split("/")[-1] or folder_name(short, paper.key)
            d = cfg.library_root / rec.get("dir", "") if rec.get("dir") else None
            ft = d / "full.md" if d else None
            bilingual = bool(ft and ft.is_file() and re.search(r"[\u4e00-\u9fff]", ft.read_text(encoding="utf-8", errors="ignore")))
            has_ft = bool(ft and ft.is_file())
            note = bool(d and (d / "note.md").is_file())
            summary = bool(d and (d / "summary.md").is_file())
            marks = ("F" if bilingual else ("f" if has_ft else "-")) + ("N" if note else "-") + ("S" if summary else "-")
            print(f"  {folder:30s} [{marks}]")
    print("\n图例: F=中英全文 f=仅英文 N=精读笔记 S=速览；- 表示未生成")

    missing = [p.key for p in usable if p.key not in recorded]
    if missing:
        print(f"未建目录: {len(missing)}（{', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}）")
    return 0


def cmd_extract(cfg, args) -> int:
    from .extract import extract_paper

    state = load_state(cfg)
    rec = state.get("papers", {}).get(args.key)
    if not rec:
        print(f"[error] 论文 {args.key} 没有目录记录，请先 `paperread plan --apply`", file=sys.stderr)
        return 1
    paper_dir = cfg.library_root / rec["dir"]
    assets = paper_dir / "assets"
    if args.force and assets.is_dir():
        shutil.rmtree(assets)

    res = extract_paper(cfg, args.key, backend=args.backend)
    total = sum(p.stat().st_size for p in res.assets)
    print(f"[extract] {res.short}-{res.key}  ({res.pages} 页)")
    print(f"  块 {res.blocks}  图 {res.figures}  公式 {res.equations}  assets {len(res.assets)} 个文件 / {_fmt_size(total)}")
    print(f"  源文件: {res.src_md}")
    print(f"  块清单: {res.blocks_json}")
    if res.equations:
        print("  提示: 公式以图片保存（不翻译），图注已按 Figure/Table 编号重命名")
    print("  提示: 重新抽取后请重跑 translate / packet，保持全文与写作包同步")
    return 0


def cmd_audit(cfg, args) -> int:
    from .plan import _is_ignored
    from .zotero_api import ping

    lib = load_library_auto(cfg)
    source = "Zotero 本地 API" if (cfg.prefer_local_api and ping(cfg)) else "sqlite 只读副本"
    print(f"数据源: {source}")
    print(f"集合 {len(lib.collections)} 个 / 论文条目 {len(lib.papers)} 篇 / 回收站 {lib.deleted_count}")
    print()

    usable: list = []
    missing: list = []
    orphan_usable: list = []
    ignored: list = []
    for p in lib.papers:
        if _is_ignored(p, lib, cfg):
            ignored.append(p)
            continue
        if not p.collection_ids:
            (orphan_usable if p.pdf and p.pdf.exists else missing).append(p)
        elif not p.pdf or not p.pdf.exists:
            missing.append(p)
        else:
            usable.append(p)

    print(f"[可用] {len(usable)} 篇")
    for col in lib.collections_with_papers():
        rows = [p for p in usable if col.collection_id in p.collection_ids]
        if rows:
            print(f"  {col.display_path}/  ({len(rows)})")
    print()

    if missing:
        print(f"[无 PDF / 文件缺失] {len(missing)} 篇（不进 vault；如需彻底忽略，把 key 加入 config [ignore].items）")
        for p in missing:
            cols = ", ".join(c.display_path for c in lib.collections_of(p)) or "未归集"
            print(f"  - {p.key}  {p.title[:66]}  ({cols})")
        print()

    if orphan_usable:
        print(f"[孤儿：有 PDF 但未归集] {len(orphan_usable)} 篇（建议在 Zotero 里归入集合，否则不会进 vault）")
        for p in orphan_usable:
            print(f"  - {p.key}  {p.title[:66]}")
        print()

    if ignored:
        print(f"[已忽略] {len(ignored)} 篇（config [ignore] 命中）")
        for p in ignored[:10]:
            print(f"  - {p.key}  {p.title[:66]}")
        print()

    state = load_state(cfg)
    papers_state = state.get("papers", {})
    print(f"[vault] 已登记目录 {len(papers_state)} 个")
    return 0


def cmd_translate(cfg, args) -> int:
    from .translate import emit_translation_tasks, import_translations, translate_paper

    if args.emit_tasks:
        if not args.key:
            print("[error] --emit-tasks 需要指定 key", file=sys.stderr)
            return 1
        paths = emit_translation_tasks(cfg, args.key, main_only=args.main_only)
        print(f"[translate] 待翻译任务文件 {len(paths)} 个：")
        for p in paths:
            print(f"  {p}")
        return 0

    if args.import_json:
        if not args.key:
            print("[error] --import 需要指定 key", file=sys.stderr)
            return 1
        n = import_translations(cfg, args.key, Path(args.import_json))
        res = translate_paper(cfg, args.key, provider="agent", main_only=args.main_only, cache_only=True)
        print(f"[translate] 导入 {n} 条译文 → {res.fulltext.name}（agent 缓存）")
        return 0

    keys: list[str]
    if args.all:
        keys = list(load_state(cfg).get("papers", {}).keys())
    elif args.collection:
        keys = [k for k, v in load_state(cfg).get("papers", {}).items() if v.get("collection") == args.collection]
    else:
        keys = [args.key] if args.key else []
    if not keys:
        print("[error] 需要指定 key、--collection 或 --all", file=sys.stderr)
        return 1

    failures = 0
    for key in keys:
        try:
            res = translate_paper(
                cfg,
                key,
                dry_run=args.dry_run,
                max_blocks=args.max_blocks,
                provider=args.provider,
                main_only=args.main_only,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[translate] {key}  [ERROR] {type(exc).__name__}: {exc}")
            failures += 1
            continue
        if res.mode == "dry-run":
            print(f"[translate] {res.short}-{res.key}  dry-run（英文+锚点）→ {res.fulltext.name}")
        else:
            print(
                f"[translate] {res.short}-{res.key}  {res.translated}/{res.blocks_total} 块  "
                f"字符 {res.chars_in}->{res.chars_out}  → {res.fulltext.name}"
            )
    if len(keys) > 1:
        print(f"\n完成 {len(keys) - failures}/{len(keys)} 篇，失败 {failures}")
    return 1 if failures else 0


def cmd_index(cfg, args) -> int:
    from datetime import datetime

    from .plan import _is_ignored

    lib = load_library_auto(cfg)
    state = load_state(cfg)
    recorded = state.get("papers", {})

    groups: dict[str, list] = {}
    for paper in lib.papers:
        if _is_ignored(paper, lib, cfg):
            continue
        col = lib.main_collection(paper)
        groups.setdefault(col.display_path if col else "(未归集)", []).append(paper)

    lines = [
        "---",
        "title: 论文索引",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 论文索引",
        "",
        f"> 由 `paperread index` 自动生成；共 {sum(len(v) for v in groups.values())} 篇，{len(groups)} 个集合。",
        "",
    ]
    for col in sorted(groups):
        lines.append(f"## {col}")
        lines.append("")
        for paper in sorted(groups[col], key=lambda p: p.title.lower()):
            rec = recorded.get(paper.key) or {}
            short = rec.get("short") or guess_short(paper.title, paper.key, cfg.names)
            reldir = rec.get("dir", "")
            folder = reldir.split("/")[-1] or folder_name(short, paper.key)
            d = cfg.library_root / reldir if reldir else None
            base = f"{reldir}/" if reldir else ""
            note = f"[精读]({base}note.md)" if d and (d / "note.md").is_file() else "精读（缺）"
            summ = f"[速览]({base}summary.md)" if d and (d / "summary.md").is_file() else "速览（缺）"
            ft = f"[全文]({base}full.md)" if d and (d / "full.md").is_file() else "全文（缺）"
            pub = (paper.publication or "").strip()
            if paper.year and not pub.startswith(paper.year):
                pub = f"{paper.year} {pub}".strip()
            suffix = f" · {pub}" if pub else ""
            lines.append(f"- **{short}**{suffix} · {note} · {summ} · {ft}")
        lines.append("")
    out = cfg.library_root / "_索引.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[index] 已生成 {out}（{sum(len(v) for v in groups.values())} 篇）")
    return 0


def cmd_formulas(cfg, args) -> int:
    from .formulas import emit_formula_tasks, import_formulas

    if args.emit_tasks:
        out = emit_formula_tasks(cfg, args.key)
        print(f"[formulas] 待识别公式任务: {out}")
        return 0
    if args.import_json:
        n, fulltext = import_formulas(cfg, args.key, Path(args.import_json))
        print(f"[formulas] 替换 {n} 个公式为 LaTeX → {fulltext}")
        return 0
    print("[error] 需要 --emit-tasks 或 --import-json", file=sys.stderr)
    return 1


def cmd_mathfix(cfg, args) -> int:
    from .mathfix import emit_mathfix_tasks, import_mathfix

    if args.emit_tasks:
        paths = emit_mathfix_tasks(cfg, args.key)
        print(f"[mathfix] 待修正译文任务 {len(paths)} 个：")
        for p in paths:
            print(f"  {p}")
        return 0
    if args.import_json:
        n, fulltext = import_mathfix(cfg, args.key, Path(args.import_json))
        print(f"[mathfix] 更新 {n} 条译文 → {fulltext}")
        return 0
    print("[error] 需要 --emit-tasks 或 --import-json", file=sys.stderr)
    return 1


def cmd_tables(cfg, args) -> int:
    from .tables import import_tables, upgrade_tables

    if args.import_json:
        n, fulltext = import_tables(cfg, args.key, Path(args.import_json))
        print(f"[tables] 替换 {n} 张表格 → {fulltext}")
        return 0
    replaced, total, warnings = upgrade_tables(cfg, args.key)
    print(f"[tables] {replaced}/{total} 张表格已升级（MinerU HTML，锚点保留）")
    for w in warnings:
        print(f"  [WARN] {w}")
    return 0


def cmd_fixsrc(cfg, args) -> int:
    from .mathfix import fix_source

    n, fulltext = fix_source(cfg, args.key, Path(args.import_json))
    print(f"[fixsrc] 修正 {n} 段源文本 → {fulltext}")
    return 0


def cmd_page(cfg, args) -> int:
    """把 PDF 某一页渲染成 PNG（供 agent 读图核对公式/表格）。"""
    from .tables import _pdf_path as pdf_path_for

    pdf = pdf_path_for(cfg, args.key)
    import fitz

    doc = fitz.open(pdf)
    if not (1 <= args.page <= doc.page_count):
        print(f"[error] 页码超范围（1..{doc.page_count}）", file=sys.stderr)
        return 1
    out_dir = cfg.state_dir / "work" / args.key / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"p{args.page:03d}.png"
    pix = doc[args.page - 1].get_pixmap(dpi=args.dpi)
    pix.save(out)
    print(out)
    return 0


def cmd_init(cfg, args) -> int:
    """初始化：生成 config/settings.toml（若不存在）与 vault 目录。"""
    from .config import PROJECT_ROOT

    example = PROJECT_ROOT / "config" / "settings.example.toml"
    target = PROJECT_ROOT / "config" / "settings.toml"
    if target.exists() and not args.force:
        print(f"[init] 已存在 {target}（加 --force 覆盖）")
    else:
        target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[init] 已生成 {target}")
    cfg.vault_root.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    print(f"[init] vault: {cfg.vault_root}")
    print("[init] 下一步：")
    print("  1) 编辑 config/settings.toml：vault.root / zotero.data_dir（可留空自动探测）")
    print("  2) Zotero → 设置 → 高级 → 勾选「允许其他应用与 Zotero 通信」")
    print("  3) paperread doctor   # 自检")
    print("  4) paperread scan && paperread plan --apply")
    return 0


def cmd_doctor(cfg, args) -> int:
    """环境自检：配置 / Zotero / vault / API key / 依赖。"""
    import urllib.request

    from .config import PROJECT_ROOT

    ok = True

    def check(name: str, passed: bool, detail: str = "", optional: bool = False) -> None:
        nonlocal ok
        if not optional:
            ok = ok and passed
        mark = "OK  " if passed else ("WARN" if optional else "FAIL")
        print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))

    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    check("配置文件", bool(cfg.source_path), str(cfg.source_path or "未找到 config/settings.toml（先跑 paperread init）"))
    try:
        import fitz  # noqa: F401

        check("PyMuPDF", True)
    except Exception as exc:  # noqa: BLE001
        check("PyMuPDF", False, str(exc))

    zdir = cfg.zotero_data_dir
    check("Zotero 数据目录", (zdir / "zotero.sqlite").is_file(), str(zdir))
    api_ok = False
    try:
        req = urllib.request.Request(cfg.local_api.rstrip("/") + "/users/0/items?limit=1", headers={"Zotero-API-Version": "3"})
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
            api_ok = resp.status == 200
    except Exception:  # noqa: BLE001
        api_ok = False
    check("Zotero 本地 API", api_ok, cfg.local_api + ("（未开启时回退 sqlite 只读副本）" if not api_ok else ""), optional=True)

    try:
        probe = cfg.vault_root / ".paperread" / ".write-test"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check("vault 可写", True, str(cfg.vault_root))
    except Exception as exc:  # noqa: BLE001
        check("vault 可写", False, str(exc))

    provider = cfg.translate_provider
    if provider == "agent":
        check("翻译 provider=agent", True, "由 agent 逐块翻译，无需 API key")
    elif provider == "baidu":
        check("百度翻译凭据", bool(cfg.baidu_appid and cfg.baidu_secret), "appid/secret 见 config/settings.toml")
    else:
        check("DeepSeek key", bool(cfg.api_key()), "config/api_key、环境变量或 DSH 凭据")
    check("MinerU key（可选）", bool(cfg.mineru_api_key), "未配置时只能用 pymupdf 后端")
    skill = PROJECT_ROOT / ".agents" / "skills" / "paper-read" / "SKILL.md"
    check("DSH skill", skill.is_file(), str(skill))
    print("\n[doctor] 全部通过" if ok else "\n[doctor] 有未通过项，按上面提示修复")
    return 0 if ok else 1


def cmd_packet(cfg, args) -> int:
    from .packet import build_packet

    out = build_packet(cfg, args.key)
    print(f"[packet] 写作包: {out}")
    return 0


def cmd_verify(cfg, args) -> int:
    from .verify import render_report, verify_paper

    keys: list[str]
    if args.all:
        state = load_state(cfg)
        keys = list(state.get("papers", {}).keys())
    else:
        keys = [args.key]

    failed = 0
    for key in keys:
        try:
            res = verify_paper(cfg, key)
        except Exception as exc:  # noqa: BLE001
            print(f"[verify] {key}  [ERROR] {exc}")
            failed += 1
            continue
        print(render_report(res))
        print()
        if not res.ok:
            failed += 1
    return 1 if failed else 0


def cmd_run(cfg, args) -> int:
    state = load_state(cfg)
    papers = state.get("papers", {})
    if args.key:
        keys = [args.key]
    elif args.collection:
        keys = [k for k, v in papers.items() if v.get("collection") == args.collection]
    else:
        print("[error] 需要指定 key 或 --collection", file=sys.stderr)
        return 1
    if not keys:
        print("[error] 没有匹配的论文", file=sys.stderr)
        return 1

    for key in keys:
        rc = _pipeline_one(cfg, key, backend=args.backend, dry_run=args.dry_run, force=args.force, formulas=args.formulas)
        if rc:
            return rc
    return 0


def _pipeline_one(
    cfg, key: str, *, backend=None, dry_run: bool = False, force: bool = False, formulas: bool = False
) -> int:
    """单篇 extract -> [formulas 任务] -> translate -> packet。"""
    from .extract import extract_paper
    from .formulas import emit_formula_tasks
    from .packet import build_packet
    from .translate import translate_paper

    state = load_state(cfg)
    rec = state.get("papers", {}).get(key, {})
    if not rec:
        print(f"[error] 状态里没有 {key}，先跑 `paperread add <标题/key>` 或 `paperread plan --apply`", file=sys.stderr)
        return 1
    short = rec.get("short", key)
    paper_dir = cfg.library_root / rec.get("dir", "")
    print(f"== {short}-{key}")
    src = paper_dir / "src.md"
    if force or not src.is_file():
        res = extract_paper(cfg, key, backend=backend)
        print(f"  extract: {res.pages} 页, 图 {res.figures}, 公式 {res.equations}, assets {len(res.assets)}")
    else:
        print("  extract: 跳过（已存在源文件，--force 可重抽）")
    if formulas:
        task = emit_formula_tasks(cfg, key)
        print(f"  formulas: 已导出公式任务 {task}（agent 识别后 --import-json 导入；MinerU 后端解析则自带 LaTeX）")
    if cfg.translate_provider == "agent" and not dry_run:
        # agent 路线不调用外部 API：先渲染英文全文，翻译由 agent 用 emit-tasks/import-json 完成
        print("  translate: provider=agent，先渲染英文+锚点（之后用 --emit-tasks 交给 agent 翻译）")
        dry_run = True
    tr = translate_paper(cfg, key, dry_run=dry_run)
    if tr.mode == "dry-run":
        print("  translate: dry-run（英文+锚点）")
    else:
        print(f"  translate: {tr.translated}/{tr.blocks_total} 块, 字符 {tr.chars_in}->{tr.chars_out}")
    pkt = build_packet(cfg, key)
    print(f"  packet: {pkt}")
    return 0


def cmd_find(cfg, args) -> int:
    """按 key / 标题 / DOI / 作者在 Zotero 库里查找（只读，不建目录）。"""
    from .ingest import search_papers

    lib = load_library_auto(cfg)
    matches = search_papers(lib, args.query)
    if not matches:
        print(f"[find] 没有匹配「{args.query}」的论文")
        return 1
    print(f"[find] {len(matches)} 条匹配：")
    for p in matches[:30]:
        short = guess_short(p.title, p.key, cfg.names)
        col = lib.main_collection(p)
        col_name = "/".join(col.path) if col else "(未归集)"
        pdf = "PDF✓" if p.pdf and p.pdf.exists else "PDF✗"
        print(f"  {p.key}  {short:22s} {p.year:4s}  {col_name:20s} {pdf}  {p.title[:70]}")
    return 0


def cmd_add(cfg, args) -> int:
    """按 key / 标题 / DOI 添加单篇到 vault（不扫描全库、不建其它论文目录）。"""
    from .ingest import resolve_one

    lib = load_library_auto(cfg)
    paper, matches = resolve_one(lib, args.query)
    if paper is None:
        if matches:
            print(f"[add] 「{args.query}」匹配到 {len(matches)} 篇，请用 key 精确指定：")
            for p in matches[:20]:
                print(f"  {p.key}  {p.title[:80]}")
        else:
            print(f"[add] 没有找到「{args.query}」；可以先用 `paperread find <关键词>` 查看", file=sys.stderr)
        return 1
    plan = build_plan(lib, cfg, load_state(cfg), keys={paper.key})
    if not plan.entries:
        print(f"[add] {paper.key} 被 [ignore] 规则忽略", file=sys.stderr)
        return 1
    entry = plan.entries[0]
    print(f"== {entry.short}-{paper.key}")
    print(f"   标题: {entry.title}")
    print(f"   目录: {entry.target}")
    print(f"   状态: {entry.status}（{entry.detail}）")
    if entry.status == "no_pdf":
        print("[add] 没有可用的 PDF 附件，先把这个 PDF 加进 Zotero 再试", file=sys.stderr)
        return 1
    if entry.status != "exists":
        created = apply_plan(plan, cfg, load_state(cfg))
        print(f"   已创建目录 {len(created)} 个")
    if args.run:
        return _pipeline_one(cfg, paper.key, backend=args.backend, dry_run=args.dry_run, formulas=args.formulas)
    print("   下一步：paperread extract <key> / paperread translate <key> / paperread packet <key>")
    return 0


def cmd_ingest(cfg, args) -> int:
    """把 arXiv / DOI 论文写入 Zotero（可选集合），再按需处理进 vault。"""
    from .ingest import ingest_paper, resolve_one

    try:
        key, meta = ingest_paper(cfg, args.identifier, collection=args.collection)
    except Exception as exc:  # noqa: BLE001
        print(f"[ingest] 失败：{exc}", file=sys.stderr)
        return 1
    print(f"[ingest] 已写入 Zotero: {key}  {meta.title}")
    if args.no_process:
        return 0
    lib = load_library_auto(cfg)
    paper, _ = resolve_one(lib, key)
    if paper is None:
        print("[ingest] 条目已入库，但暂时读不到（Zotero 可能还没同步）；稍后 `paperread add <key>`")
        return 0
    plan = build_plan(lib, cfg, load_state(cfg), keys={key})
    if plan.entries and plan.entries[0].status != "exists":
        created = apply_plan(plan, cfg, load_state(cfg))
        print(f"[ingest] 已创建目录 {len(created)} 个")
    if args.run:
        return _pipeline_one(cfg, key, backend=args.backend, dry_run=args.dry_run, formulas=args.formulas)
    return 0


def cmd_notesync(cfg, args) -> int:
    """把笔记里已被升级为 LaTeX 的公式图替换成 $$...$$。"""
    from .notesync import sync_notes

    if args.all:
        keys = list(load_state(cfg).get("papers", {}).keys())
    elif args.key:
        keys = [args.key]
    else:
        print("[error] 需要指定 key 或 --all", file=sys.stderr)
        return 1
    total = 0
    for key in keys:
        try:
            n, kept, files = sync_notes(cfg, key, apply=not args.dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"[notesync] {key} 跳过：{exc}")
            continue
        if n or kept:
            mode = "预览" if args.dry_run else "已写"
            print(f"[notesync] {key}: {mode}替换 {n} 张公式图，保留 {kept} 张其它图 {files}")
        total += n
    print(f"[notesync] 共替换 {total} 张公式图" + ("（dry-run，未写文件）" if args.dry_run else ""))
    return 0


def cmd_search(cfg, args) -> int:
    """在线检索 arXiv / Crossref，输出候选列表（不写库、不建目录）。"""
    import re
    from datetime import datetime

    from .search import search

    cands = search(args.query, limit=args.limit, source=args.source)
    if not cands:
        print(f"[search] 没有找到「{args.query}」的候选论文")
        return 1
    out_dir = cfg.state_dir / "searches"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", args.query).strip("-")[:40] or "search"
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.json"
    save_json(out, {"query": args.query, "generated_at": datetime.now().isoformat(timespec="seconds"), "candidates": [c.to_dict() for c in cands]})
    print(f"[search] {len(cands)} 条候选（已保存：{out}）")
    for i, c in enumerate(cands, 1):
        ident = c.arxiv_id or c.doi or ""
        print(f"  {i:2d}. [{c.year or '----'}] {c.title[:78]}")
        print(f"      {c.venue or c.source}  {ident}")
    print("\n下一步：paperread collect <上面保存的 json> --indices 1,2,3 --collection <集合> [--run]")
    return 0


def cmd_collect(cfg, args) -> int:
    """把 search 结果里的指定候选批量入库到 Zotero，并可接着处理进 vault。"""
    from .ingest import ingest_metadata, resolve_one
    from .search import Candidate

    data = load_json(Path(args.file))
    if not isinstance(data, dict) or not data.get("candidates"):
        print(f"[collect] 文件里没有 candidates：{args.file}", file=sys.stderr)
        return 1
    cands = [Candidate.from_dict(c) for c in data["candidates"]]
    if args.indices:
        selected = []
        for part in args.indices.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                selected.extend(range(int(a), int(b) + 1))
            else:
                selected.append(int(part))
        picks = [cands[i - 1] for i in selected if 1 <= i <= len(cands)]
    else:
        picks = cands[: args.limit]
    if not picks:
        print("[collect] 没有选中任何候选", file=sys.stderr)
        return 1
    keys: list[str] = []
    for c in picks:
        try:
            key, _ = ingest_metadata(cfg, c.to_meta(), collection=args.collection)
            keys.append(key)
            print(f"[collect] 已入库 {key}  {c.title[:70]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[collect] 入库失败：{c.title[:60]} → {exc}", file=sys.stderr)
    if not keys:
        return 1
    if args.run:
        lib = load_library_auto(cfg)
        for key in keys:
            paper, _ = resolve_one(lib, key)
            if paper is None:
                print(f"[collect] {key} 暂未同步，稍后 `paperread add {key}`")
                continue
            plan = build_plan(lib, cfg, load_state(cfg), keys={key})
            if plan.entries and plan.entries[0].status != "exists":
                apply_plan(plan, cfg, load_state(cfg))
            _pipeline_one(cfg, key, backend=args.backend, dry_run=args.dry_run, formulas=args.formulas)
    print(f"[collect] 完成：入库 {len(keys)} 篇；接着可 `paperread packet <key>` 让 agent 写笔记")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperread", description="Zotero -> Obsidian 论文阅读流水线")
    parser.add_argument("--version", action="version", version=f"paperread {__version__}")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config/settings.toml）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", aliases=["扫描"], help="只读扫描 Zotero 库，输出清单与增量信息")
    p_scan.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    p_scan.set_defaults(func=cmd_scan)

    p_plan = sub.add_parser("plan", aliases=["规划"], help="对比 vault 现状，输出目录规划（预览）")
    p_plan.add_argument("--json", action="store_true", help="输出完整 JSON 计划")
    p_plan.add_argument("--apply", action="store_true", help="创建目录并写状态（不写笔记）")
    p_plan.set_defaults(func=cmd_plan)

    p_status = sub.add_parser("status", aliases=["状态"], help="查看 vault 覆盖率")
    p_status.set_defaults(func=cmd_status)

    p_audit = sub.add_parser("audit", aliases=["审计"], help="审计 Zotero 库：可用/缺 PDF/孤儿/已忽略")
    p_audit.set_defaults(func=cmd_audit)

    p_extract = sub.add_parser("extract", aliases=["抽取"], help="PDF -> 英文源 markdown + assets + blocks.json")
    p_extract.add_argument("key", help="Zotero item key（8 位）")
    p_extract.add_argument("--force", action="store_true", help="清空 assets 后重新抽取")
    p_extract.add_argument("--backend", choices=["pymupdf", "mineru"], default=None, help="解析后端（默认取 config [extract].backend）")
    p_extract.set_defaults(func=cmd_extract)

    p_tr = sub.add_parser("translate", aliases=["翻译"], help="blocks.json -> 中英对照全文（段级交替 + 锚点）")
    p_tr.add_argument("key", nargs="?", help="Zotero item key（8 位）")
    p_tr.add_argument("--all", action="store_true", help="翻译所有已登记论文")
    p_tr.add_argument("--collection", help="按集合批量（如 MyPapers）")
    p_tr.add_argument("--dry-run", action="store_true", help="不调用 API，只渲染英文 + 锚点")
    p_tr.add_argument("--max-blocks", type=int, default=None, help="只翻译前 N 块（冒烟测试用）")
    p_tr.add_argument("--provider", choices=["deepseek", "baidu", "agent"], default=None, help="覆盖配置里的翻译 provider")
    p_tr.add_argument("--main-only", action="store_true", help="只翻正文（到 References 之前），不翻附录")
    p_tr.add_argument("--emit-tasks", action="store_true", help="导出待翻译块给 agent（不调用 API）")
    p_tr.add_argument("--import-json", default=None, help="导入 agent 译文 JSON（{block_id: 中文}）并渲染全文")
    p_tr.set_defaults(func=cmd_translate)

    p_idx = sub.add_parser("index", aliases=["索引"], help="生成 vault 根目录的 _索引.md（集合树 + 链接）")
    p_idx.set_defaults(func=cmd_index)

    p_pk = sub.add_parser("packet", aliases=["写作包"], help="生成笔记写作包（大纲/图清单/元数据）")
    p_pk.add_argument("key", help="Zotero item key（8 位）")
    p_pk.set_defaults(func=cmd_packet)

    p_fm = sub.add_parser("formulas", aliases=["公式"], help="公式升级：assets/eq*.png -> LaTeX（保留锚点）")
    p_fm.add_argument("key", help="Zotero item key（8 位）")
    p_fm.add_argument("--emit-tasks", action="store_true", help="导出待识别公式任务")
    p_fm.add_argument("--import-json", default=None, help="导入 {block_id: latex} 并重渲染 full.md")
    p_fm.set_defaults(func=cmd_formulas)

    p_mf = sub.add_parser("mathfix", aliases=["行内公式"], help="行内公式修正：中文译文里的数学片段 -> $...$")
    p_mf.add_argument("key", help="Zotero item key（8 位）")
    p_mf.add_argument("--emit-tasks", action="store_true", help="导出待修正译文")
    p_mf.add_argument("--import-json", default=None, help="导入 {block_id: 新中文} 并重渲染")
    p_mf.set_defaults(func=cmd_mathfix)

    p_tb = sub.add_parser("tables", aliases=["表格"], help="表格升级：MinerU HTML 表格（保留 rowspan/colspan 与锚点）")
    p_tb.add_argument("key", help="Zotero item key（8 位）")
    p_tb.add_argument("--import-json", default=None, help="导入 {block_id: 表格文本}")
    p_tb.set_defaults(func=cmd_tables)

    p_fs = sub.add_parser("fixsrc", aliases=["修源"], help="修正被 PDF 抽取破坏的源段落（同时重映射译文缓存）")
    p_fs.add_argument("key", help="Zotero item key（8 位）")
    p_fs.add_argument("--import-json", required=True, help="{block_id: {src, zh}}")
    p_fs.set_defaults(func=cmd_fixsrc)

    p_init = sub.add_parser("init", aliases=["初始化"], help="初始化配置与 vault 目录")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的 config/settings.toml")
    p_init.set_defaults(func=cmd_init)

    p_doc = sub.add_parser("doctor", aliases=["自检"], help="环境自检（配置/Zotero/vault/API key/依赖）")
    p_doc.set_defaults(func=cmd_doctor)

    p_pg = sub.add_parser("page", aliases=["页面"], help="把 PDF 某页渲染成 PNG（供 agent 核对公式/表格）")
    p_pg.add_argument("key", help="Zotero item key（8 位）")
    p_pg.add_argument("page", type=int, help="页码（1-based）")
    p_pg.add_argument("--dpi", type=int, default=150, help="渲染 DPI（默认 150）")
    p_pg.set_defaults(func=cmd_page)

    p_vf = sub.add_parser("verify", aliases=["校验"], help="校验笔记链接/锚点/图片/引文")
    p_vf.add_argument("key", nargs="?", help="Zotero item key（8 位）")
    p_vf.add_argument("--all", action="store_true", help="校验所有已登记论文")
    p_vf.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", aliases=["一键运行"], help="单篇/整集合跑 extract -> translate -> packet")
    p_run.add_argument("key", nargs="?", help="Zotero item key（8 位）")
    p_run.add_argument("--collection", help="按集合名批量（如 MyPapers）")
    p_run.add_argument("--dry-run", action="store_true", help="翻译只渲染英文+锚点")
    p_run.add_argument("--force", action="store_true", help="强制重新抽取")
    p_run.add_argument("--backend", choices=["pymupdf", "mineru"], default=None, help="解析后端（默认取 config [extract].backend）")
    p_run.add_argument("--formulas", action="store_true", help="导出公式任务（agent 识别后升级为 LaTeX）")
    p_run.set_defaults(func=cmd_run)

    p_find = sub.add_parser("find", aliases=["查找"], help="在 Zotero 库里按 key/标题/DOI/作者查找（只读）")
    p_find.add_argument("query", help="key / 标题片段 / DOI / 作者")
    p_find.set_defaults(func=cmd_find)

    p_add = sub.add_parser("add", aliases=["添加"], help="添加单篇论文到 vault（不扫描全库）")
    p_add.add_argument("query", help="key / 标题片段 / DOI")
    p_add.add_argument("--run", action="store_true", help="建目录后立刻跑 extract -> translate -> packet")
    p_add.add_argument("--dry-run", action="store_true", help="翻译只渲染英文+锚点")
    p_add.add_argument("--backend", choices=["pymupdf", "mineru"], default=None, help="解析后端")
    p_add.add_argument("--formulas", action="store_true", help="导出公式任务（agent 识别后升级为 LaTeX）")
    p_add.set_defaults(func=cmd_add)

    p_ing = sub.add_parser("ingest", aliases=["入库"], help="把 arXiv/DOI 论文写入 Zotero（可选集合），再处理进 vault")
    p_ing.add_argument("identifier", help="arXiv ID / DOI / URL")
    p_ing.add_argument("--collection", help="Zotero 集合名（写入后移动到该集合）")
    p_ing.add_argument("--no-process", action="store_true", help="只写入 Zotero，不建 Obsidian 目录")
    p_ing.add_argument("--run", action="store_true", help="建目录后立刻跑 extract -> translate -> packet")
    p_ing.add_argument("--dry-run", action="store_true", help="翻译只渲染英文+锚点")
    p_ing.add_argument("--backend", choices=["pymupdf", "mineru"], default=None, help="解析后端")
    p_ing.add_argument("--formulas", action="store_true", help="导出公式任务（agent 识别后升级为 LaTeX）")
    p_ing.set_defaults(func=cmd_ingest)

    p_ns = sub.add_parser("notesync", aliases=["笔记同步"], help="把笔记里已升级为 LaTeX 的公式图替换成 $$...$$")
    p_ns.add_argument("key", nargs="?", help="Zotero item key（8 位）")
    p_ns.add_argument("--all", action="store_true", help="处理所有已登记论文")
    p_ns.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    p_ns.set_defaults(func=cmd_notesync)

    p_srch = sub.add_parser("search", aliases=["搜索"], help="在线检索 arXiv/Crossref 候选论文（不写库）")
    p_srch.add_argument("query", help="关键词（标题/主题/作者）")
    p_srch.add_argument("--limit", type=int, default=10, help="返回条数（默认 10）")
    p_srch.add_argument("--source", choices=["arxiv", "crossref", "all"], default="all", help="检索源")
    p_srch.set_defaults(func=cmd_search)

    p_col = sub.add_parser("collect", aliases=["批量入库"], help="把 search 结果的候选批量入库（可选 --run 直接处理）")
    p_col.add_argument("file", help="search 保存的 JSON 文件")
    p_col.add_argument("--indices", help="要入库的序号，如 1,3-5（默认取前 --limit 条）")
    p_col.add_argument("--limit", type=int, default=5, help="不指定 --indices 时取前 N 条")
    p_col.add_argument("--collection", help="Zotero 集合名（写入后移动过去）")
    p_col.add_argument("--run", action="store_true", help="入库后立刻 extract -> translate -> packet")
    p_col.add_argument("--dry-run", action="store_true", help="翻译只渲染英文+锚点")
    p_col.add_argument("--backend", choices=["pymupdf", "mineru"], default=None, help="解析后端")
    p_col.add_argument("--formulas", action="store_true", help="导出公式任务（agent 识别后升级为 LaTeX）")
    p_col.set_defaults(func=cmd_collect)
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        return args.func(cfg, args)
    except Exception as exc:  # 简洁错误输出，避免长堆栈吓人
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
