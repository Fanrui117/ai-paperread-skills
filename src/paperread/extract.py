"""PDF 抽取：PDF -> 结构化 markdown（英文源）+ assets 图片 + blocks.json。

产物：
  <论文目录>/src.md     英文源（翻译阶段的输入）
  <论文目录>/assets/fig<N>_<slug>.png   图（按图注编号重命名）
  <论文目录>/assets/eq<N>_p<page>.png   公式图
  vault/.paperread/work/<key>/blocks.json  块清单（翻译/校验用）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import pymupdf4llm

from .config import Config
from .plan import load_state, save_json
from .zotero import load_library_auto

IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
PICTURE_TEXT_RE = re.compile(r"<!--\s*Start of picture text\s*-->.*?<!--\s*End of picture text\s*-->", re.S)
CAPTION_RE = re.compile(r"^(?:Figure|Fig\.)\s*(\d+)\s*[:.|：]", re.I | re.M)
TABLE_CAPTION_RE = re.compile(r"^Table\s*(\d+)\s*[:.|：]", re.I | re.M)
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
PAGE_MARK = "<!-- page {n} -->"

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "with", "by",
    "at", "from", "as", "is", "are", "using", "via", "into", "over",
}


@dataclass
class ExtractResult:
    key: str
    short: str
    paper_dir: Path
    src_md: Path
    blocks_json: Path
    pages: int = 0
    figures: int = 0
    equations: int = 0
    blocks: int = 0
    assets: list[Path] = field(default_factory=list)


def _slug_words(text: str, limit: int = 5) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    words = [w for w in words if w not in STOPWORDS]
    return "_".join(words[:limit]) or "x"


def _clean_heading(line: str) -> str:
    m = HEADING_RE.match(line)
    if not m:
        return line
    hashes, body = m.group(1), m.group(2)
    body = body.replace("**", "").replace("__", "").strip()
    return f"{hashes} {body}"


def _page_at(text: str, pos: int) -> int:
    """pos 之前最近的 page 标记里的页码。"""
    pages = [int(m.group(1)) for m in re.finditer(r"<!-- page (\d+) -->", text[:pos])]
    return pages[-1] if pages else 1


def _next_caption(text: str, pos: int, window: int = 900) -> tuple[str, int, int] | None:
    """从 pos 往后找最近的图注（Figure N: / Table N:），限制在 window 字符或下一个标题之前。"""
    tail = text[pos : pos + window]
    heading = re.search(r"^#{1,6} ", tail, re.M)
    if heading:
        tail = tail[: heading.start()]
    best = None
    for m in CAPTION_RE.finditer(tail):
        best = ("figure", int(m.group(1)), pos + m.start())
        break
    for m in TABLE_CAPTION_RE.finditer(tail):
        cand = ("table", int(m.group(1)), pos + m.start())
        if best is None or cand[2] < best[2]:
            best = cand
        break
    return best


def _dedupe_name(assets: Path, name: str) -> str:
    p = assets / name
    if not p.exists():
        return name
    stem, suffix = os.path.splitext(name)
    i = 2
    while (assets / f"{stem}_{i}{suffix}").exists():
        i += 1
    return f"{stem}_{i}{suffix}"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def _process_images(md: str, src_dir: Path, assets: Path, short: str) -> tuple[str, int, int]:
    """把 ![](...) 从临时目录搬到 assets，并重命名为 fig<N>_slug / eq<N>_p<page> / thumb<N>_p<page>。"""
    fig_count = 0
    eq_count = 0
    thumb_count = 0

    def repl(m: re.Match) -> str:
        nonlocal fig_count, eq_count, thumb_count
        raw = m.group(1).strip().strip("<>")
        raw = raw.replace("\\", "/")
        src_name = raw.split("/")[-1]
        src = src_dir / src_name
        if not src.is_file():
            return m.group(0)
        pos = m.start()
        page = _page_at(md, pos)
        cap = _next_caption(md, m.end())
        w, h = _image_size(src)
        size = src.stat().st_size
        if cap is None and size > 300_000:
            # 无图注的超大图：多为封面/整页扫描/装饰图，跳过
            return ""
        if cap and cap[0] == "figure":
            cap_text = md[cap[2] : cap[2] + 200]
            cap_text = re.sub(r"^(?:Figure|Fig\.)\s*\d+\s*[:.]\s*", "", cap_text, flags=re.I)
            name = _dedupe_name(assets, f"fig{cap[1]}_{_slug_words(cap_text)}.png")
            fig_count += 1
        elif cap and cap[0] == "table":
            name = _dedupe_name(assets, f"tab{cap[1]}_p{page}.png")
        elif w and h and 0.8 <= w / h <= 1.25 and w <= 400:
            # 无图注的方形小图：多为数据集/视频帧缩略图
            thumb_count += 1
            name = _dedupe_name(assets, f"thumb{thumb_count}_p{page}.png")
        elif (h and h <= 140 and w / h >= 2.2) or size < 15_000:
            # 无图注、又扁又小：多为公式裁片
            eq_count += 1
            name = _dedupe_name(assets, f"eq{eq_count}_p{page}.png")
        else:
            # 无图注的大图：多为版式解析漏掉图注的正文插图
            fig_count += 1
            name = _dedupe_name(assets, f"fig_p{page}_{fig_count}.png")
        dst = assets / name
        if src != dst:
            if dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
        return f"![[assets/{name}]]"

    new_md = IMAGE_REF_RE.sub(repl, md)
    return new_md, fig_count, eq_count


def _classify_block(block: str) -> str:
    s = block.strip()
    if not s:
        return "empty"
    if s.startswith("#"):
        return "heading"
    if s.startswith("![[assets/"):
        return "image"
    if s.startswith("$$") or (s.startswith("$") and s.count("$") >= 2 and len(s) < 400):
        return "equation"
    if s.startswith("|") or s.lstrip().startswith("<table"):
        return "table"
    if s.startswith("<!-- page"):
        return "pagebreak"
    if CAPTION_RE.match(s) or TABLE_CAPTION_RE.match(s):
        return "caption"
    return "para"


def _build_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    seq = 0
    for raw in re.split(r"\n\s*\n", md):
        kind = _classify_block(raw)
        if kind in ("empty",):
            continue
        if kind == "pagebreak":
            blocks.append({"id": "", "kind": "pagebreak", "text": raw.strip(), "page": 0})
            continue
        seq += 1
        blocks.append(
            {
                "id": f"p{seq}",
                "kind": kind,
                "text": raw.strip(),
                "page": _page_at(md, md.find(raw)),
            }
        )
    # 丢掉纯页码段落（PDF 页脚数字会被抽成独立段落）
    blocks = [b for b in blocks if not (b["kind"] == "para" and re.fullmatch(r"\d{1,3}", b["text"].strip()))]
    return blocks


def _crop_figure_above(doc, page_no: int, caption: str, assets: Path, fig_num: int, short: str) -> str | None:
    """矢量图回退：按图注位置裁出上方图形区域（图注在下、图为矢量绘制时 pymupdf4llm 抓不到）。"""
    if page_no < 1 or page_no > doc.page_count:
        return None
    page = doc[page_no - 1]
    cap_prefix = caption.strip().replace("**", "")
    cap_prefix = re.sub(r"^(Figure|Fig\.|Table)\s*\d+\s*[:.|：]\s*", "", cap_prefix)[:60]
    blocks = [b for b in page.get_text("blocks") if len(b) >= 5 and b[6] == 0]  # 文本块
    cap_block = None
    for b in blocks:
        if b[4].strip().startswith(caption.strip()[:20]) or (cap_prefix and cap_prefix[:20] in b[4]):
            cap_block = b
            break
    if cap_block is None:
        return None
    cx0, cy0, cx1, cy1 = cap_block[0], cap_block[1], cap_block[2], cap_block[3]
    # 同栏文本块：与图注横向重叠
    same_col = [
        b for b in blocks
        if b[2] > cx0 and b[0] < cx1 and not (b[0] <= cx0 and b[2] >= cx1 and b[1] == cy0)
    ]
    if not same_col:
        same_col = blocks
    col_x0 = min(b[0] for b in same_col)
    col_x1 = max(b[2] for b in same_col)
    above = [b for b in same_col if b[3] <= cy0 - 1]
    top = max((b[3] for b in above), default=page.rect.y0 + 40)
    rect = pymupdf.Rect(col_x0 - 2, top + 2, col_x1 + 2, cy0 - 2)
    if rect.height < 40 or rect.width < 80:
        return None
    name = _dedupe_name(assets, f"fig{fig_num}_{_slug_words(cap_prefix)}.png")
    pix = page.get_pixmap(clip=rect, dpi=150)
    pix.save(str(assets / name))
    return name


def _crop_vector_figures(doc, md: str, assets: Path, short: str) -> tuple[str, int]:
    """给没有内嵌图片的图注补裁图，返回 (新 md, 补裁数量)。"""
    lines = md.splitlines()
    out: list[str] = []
    page = 1
    added = 0
    for idx, line in enumerate(lines):
        m = re.match(r"^<!-- page (\d+) -->", line.strip())
        if m:
            page = int(m.group(1))
            out.append(line)
            continue
        cap = CAPTION_RE.match(line.strip())
        if cap:
            j = idx - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            prev_is_image = j >= 0 and lines[j].strip().startswith("![[")
            if not prev_is_image:
                name = _crop_figure_above(doc, page, line.strip(), assets, int(cap.group(1)), short)
                if name:
                    out.append(f"![[assets/{name}]]")
                    out.append("")
                    added += 1
        out.append(line)
    return "\n".join(out), added


def _merge_rects(rects: list, gap: float = 12.0) -> list:
    """合并相交/相邻的矩形（一张图由多个子图拼成时用）。"""
    merged = True
    while merged:
        merged = False
        out: list = []
        used = [False] * len(rects)
        for i, a in enumerate(rects):
            if used[i]:
                continue
            cur = a
            for j in range(i + 1, len(rects)):
                if used[j]:
                    continue
                b = rects[j]
                if (
                    cur.x0 - gap <= b.x1 and b.x0 - gap <= cur.x1
                    and cur.y0 - gap <= b.y1 and b.y0 - gap <= cur.y1
                ):
                    cur = pymupdf.Rect(min(cur.x0, b.x0), min(cur.y0, b.y0), max(cur.x1, b.x1), max(cur.y1, b.y1))
                    used[j] = True
                    merged = True
            used[i] = True
            out.append(cur)
        rects = out
    return rects


def _harvest_page_figures(doc, md: str, assets: Path) -> tuple[str, int]:
    """没有图注也没有内嵌图的论文（如 PIPs）：按页面大图区域裁图，插到该页开头。"""
    lines = md.splitlines()
    page_has_image: dict[int, bool] = {}
    page = 1
    for line in lines:
        m = re.match(r"^<!-- page (\d+) -->", line.strip())
        if m:
            page = int(m.group(1))
            page_has_image.setdefault(page, False)
        elif line.strip().startswith(("![[assets/fig", "![[assets/thumb", "![[assets/tab")):
            page_has_image[page] = True

    added = 0
    out: list[str] = []
    for line in lines:
        out.append(line)
        m = re.match(r"^<!-- page (\d+) -->", line.strip())
        if not m:
            continue
        pno = int(m.group(1))
        if page_has_image.get(pno, True) or pno > doc.page_count:
            continue
        pg = doc[pno - 1]
        page_area = pg.rect.get_area()
        rects: list = []
        for info in pg.get_image_info():
            r = pymupdf.Rect(info["bbox"])
            if r.width >= 20 and r.height >= 20:
                rects.append(r)
        if rects:
            rects = _merge_rects(rects, gap=15)
            rects = [r for r in rects if r.width >= 80 and r.height >= 60 and r.get_area() >= 8000]
        if not rects:
            draws = [pymupdf.Rect(d["rect"]) for d in pg.get_drawings() if d.get("rect")]
            draws = [r for r in draws if r.width >= 4 and r.height >= 4]
            if len(draws) >= 15:
                rects = _merge_rects(draws, gap=20)
                rects = [
                    r for r in rects
                    if r.width >= 100 and r.height >= 80 and r.get_area() >= 15000 and r.get_area() <= page_area * 0.8
                ]
        if not rects:
            continue
        k = 0
        for r in sorted(rects, key=lambda x: (x.y0, x.x0)):
            if r.width < 80 or r.height < 60 or r.get_area() < 8000:
                continue
            clip = pymupdf.Rect(r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4) & pg.rect
            if clip.is_empty or clip.height < 60:
                continue
            k += 1
            name = _dedupe_name(assets, f"fig_p{pno}_{k}.png")
            pg.get_pixmap(clip=clip, dpi=150).save(str(assets / name))
            out.append("")
            out.append(f"![[assets/{name}]]")
            out.append("")
            added += 1
    return "\n".join(out), added


def _extract_pymupdf(paper, assets: Path, short: str) -> tuple[str, int, int, int]:
    doc = pymupdf.open(paper.pdf.path)
    # 先写到无空格/中文的临时目录：pymupdf4llm 会把 image_path 里的空格替换成下划线，导致自己找不到图
    tmp_assets = Path(tempfile.mkdtemp(prefix="paperread_img_"))
    try:
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            write_images=True,
            image_path=str(tmp_assets),
            image_format="png",
            dpi=150,
        )
        pages = doc.page_count

        parts: list[str] = []
        for ch in chunks:
            n = (ch.get("metadata") or {}).get("page_number") or len(parts) + 1
            parts.append(f"{PAGE_MARK.format(n=n)}\n\n{ch.get('text', '').strip()}")
        md = "\n\n".join(parts)

        md = PICTURE_TEXT_RE.sub("", md)
        md = "\n".join(_clean_heading(line) for line in md.splitlines())
        md, fig_count, eq_count = _process_images(md, tmp_assets, assets, short)
        md, cropped = _crop_vector_figures(doc, md, assets, short)
        md, harvested = _harvest_page_figures(doc, md, assets)
        fig_count += cropped + harvested
        md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    finally:
        doc.close()
        shutil.rmtree(tmp_assets, ignore_errors=True)
    return md, pages, fig_count, eq_count


def _extract_mineru(cfg: Config, paper, assets: Path, short: str) -> tuple[str, int, int, int]:
    """MinerU 云端解析：公式转 LaTeX、图表/表格定位更准（只做解析，不翻译）。"""
    from .mineru import parse_pdf

    res = parse_pdf(cfg, paper.pdf.path)
    # 按文档出现顺序把 hash 文件名重命名为 imgNNN.ext，便于阅读
    order: dict[str, str] = {}
    for m in IMAGE_REF_RE.finditer(res.markdown):
        base = m.group(1).strip().strip("<>").replace("\\", "/").split("/")[-1]
        if base in order or base not in res.images:
            continue
        ext = Path(base).suffix or ".jpg"
        order[base] = f"img{len(order) + 1:03d}{ext}"
    for base, new_name in order.items():
        (assets / new_name).write_bytes(res.images[base])

    def repl(m: re.Match) -> str:
        raw = m.group(1).strip().strip("<>").replace("\\", "/")
        base = raw.split("/")[-1]
        new_name = order.get(base)
        if new_name and (assets / new_name).is_file():
            return f"![[assets/{new_name}]]"
        return ""

    md = IMAGE_REF_RE.sub(repl, res.markdown)
    md = "\n".join(_clean_heading(line) for line in md.splitlines())
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    fig_count = len(re.findall(r"!\[\[assets/", md))
    return md, 0, fig_count, 0


def extract_paper(cfg: Config, key: str, *, backend: str | None = None) -> ExtractResult:
    backend = backend or getattr(cfg, "extract_backend", "pymupdf") or "pymupdf"
    state = load_state(cfg)
    rec = state.get("papers", {}).get(key)
    if not rec:
        raise RuntimeError(f"论文 {key} 还没有目录记录，请先运行 `paperread plan --apply`")

    paper_dir = cfg.library_root / rec["dir"]
    paper_dir.mkdir(parents=True, exist_ok=True)
    assets = paper_dir / "assets"
    assets.mkdir(exist_ok=True)
    short = rec.get("short") or key

    lib = load_library_auto(cfg)
    paper = lib.paper_by_key(key)
    if paper is None or paper.pdf is None or not paper.pdf.exists:
        raise RuntimeError(f"论文 {key} 没有可用的 PDF")

    if backend == "mineru":
        md, pages, fig_count, eq_count = _extract_mineru(cfg, paper, assets, short)
    else:
        md, pages, fig_count, eq_count = _extract_pymupdf(paper, assets, short)

    # pymupdf4llm 会把单元格内换行写成 <br>，Obsidian 表格里会破坏解析，统一换成空格
    md = re.sub(r"<br\s*/?>", " ", md)

    # 记录本次使用的解析后端（换后端会导致锚点漂移，供 verify/status 提示）
    state.setdefault("papers", {}).setdefault(key, {})["backend"] = backend
    save_json(cfg.state_json, state)

    src_md = paper_dir / "src.md"
    src_md.write_text(md, encoding="utf-8")

    blocks = _build_blocks(md)
    work = cfg.state_dir / "work" / key
    work.mkdir(parents=True, exist_ok=True)
    blocks_json = work / "blocks.json"
    blocks_json.write_text(
        json.dumps({"key": key, "short": short, "blocks": blocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assets_files = sorted(p for p in assets.iterdir() if p.is_file())
    return ExtractResult(
        key=key,
        short=short,
        paper_dir=paper_dir,
        src_md=src_md,
        blocks_json=blocks_json,
        pages=pages,
        figures=fig_count,
        equations=eq_count,
        blocks=len([b for b in blocks if b["kind"] != "pagebreak"]),
        assets=assets_files,
    )
