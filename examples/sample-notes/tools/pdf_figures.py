#!/usr/bin/env python3
"""从论文 PDF 里自动裁出插图，存到 PaperNotes/assets/。

思路：先用 `pdftotext -bbox` 定位图注（"Fig. N." / "Figure N."）的坐标，再在渲染页面上
按图注所在**栏**取墨迹范围 —— 比手工估坐标可靠，也避免把另一栏的正文裁进来。

纵向有两种策略（--strategy）：
  gap    默认。从图注往上扫，先找到图的底边，再继续向上直到遇到 >= --min-gap pt 的
         连续空白。它最多只会"多裁一点"（例如把页眉多带进来一行），不会切掉图的正文。
  header 取"页面第一行文字之下 → 图注之上"的全部墨迹。若图自身顶部的标签恰好是这一页
         最靠上的一行文字，它会被误当页眉切掉（TAP-Vid、TAPIR 上都实测到过）——属于
         内容损失，比 gap 的多余边距严重，所以不作为默认。

选择依据（实测）：gap 的失败模式是"多"、header 的失败模式是"少/切"。宁可多不可少。
两种策略都可能不够好，所以裁完务必亲眼确认，必要时用 --box 手给磅值框。

一页多图时必须用 --fig N 指定图号（否则默认取本页第一张）。

依赖：poppler-utils (pdftotext, pdftoppm) + Pillow

用法：
  # 自动：裁第 3、6 页上的图，命名 <前缀>_arch.png / <前缀>_qual.png
  python3 tools/pdf_figures.py paper.pdf --prefix CoTracker \\
      --pages 3:arch,6:qual --outdir PaperNotes/assets

  # 手动给磅值框（一页多图、或自动结果不理想时）
  python3 tools/pdf_figures.py paper.pdf --prefix CoTracker --box 5:134,110,480,250 \\
      --outdir PaperNotes/assets

  # 换纵向策略 / 调空白阈值
  python3 tools/pdf_figures.py paper.pdf --prefix CoTracker --pages 3:arch \\
      --strategy header --outdir PaperNotes/assets

建议：裁完一定用图查看器（或 read_image）亲自看一眼，确认没切掉内容也没带进正文。
"""
import argparse, re, subprocess, sys
from pathlib import Path
from PIL import Image

DPI = 200
S = DPI / 72.0
CAPTION_WORDS = ("Fig.", "Fig", "Figure", "Figure.")


def render(pdf: Path, page: int, tmp: Path) -> Image.Image:
    subprocess.run(["pdftoppm", "-png", "-r", str(DPI), "-f", str(page), "-l", str(page),
                    str(pdf), str(tmp / f"pg{page:03d}")], check=True)
    f = sorted(tmp.glob(f"pg{page:03d}*.png"))[0]
    return Image.open(f).convert("RGB")


def words(pdf: Path, page: int):
    xml = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
                         capture_output=True, text=True).stdout
    return [(float(m[1]), float(m[2]), float(m[3]), float(m[4]), m[5]) for m in re.finditer(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', xml)]


def find_caption(ws):
    """返回 [(图注首词索引, 图号)]；图注 = 'Fig./Figure' + 'N.'/'N:'，且该词是所在行的行首。"""
    out = []
    for i in range(len(ws) - 1):
        if ws[i][4] not in CAPTION_WORDS:
            continue
        m = re.match(r'^(\d+)[.:]$', ws[i + 1][4])
        if not m:
            continue
        # 若同一行左侧 15pt 内还有词，说明是正文里的 "...as in Figure 3."，不是图注
        if any(abs(w[1] - ws[i][1]) < 3 and w[2] > ws[i][0] - 15 and w[0] < ws[i][0] for w in ws):
            continue
        out.append((i, int(m.group(1))))
    return out


def column_extent(ws, i):
    """图注所在栏的横向范围：从图注首词向右（再向左）合并同一行、间距 < 20pt 的连续词。"""
    cy = ws[i][1]
    row = sorted([w for w in ws if abs(w[1] - cy) < 3], key=lambda w: w[0])
    k = row.index(ws[i])
    run = [ws[i]]
    for w in row[k + 1:]:
        if w[0] - run[-1][2] < 20:
            run.append(w)
        else:
            break
    for w in reversed(row[:k]):
        if run[0][0] - w[2] < 20:
            run.insert(0, w)
        else:
            break
    return min(w[0] for w in run), max(w[2] for w in run)


def ink_bbox(img: Image.Image):
    px = img.load()
    w, h = img.size
    mnx, mny, mxx, mxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r < 245 or g < 245 or b < 245:
                mnx = min(mnx, x); mxx = max(mxx, x); mny = min(mny, y); mxy = max(mxy, y)
    return None if mxx < 0 else (mnx, mny, mxx, mxy)


def crop_auto(img: Image.Image, ws, out: Path, strategy="gap", min_gap=25.0, pad_pt=3.0, fig_num=None):
    caps = find_caption(ws)
    if not caps:
        print(f"  ! 没找到图注，请用 --box 手动给框"); return False
    if fig_num is not None:
        hit = [i for i, n in caps if n == fig_num]
        if not hit:
            found = ", ".join(str(n) for _, n in caps)
            print(f"  ! 本页只有图 {found}，没有 Figure {fig_num}"); return False
        i = hit[0]
    else:
        i = caps[0][0]
        if len(caps) > 1:
            print(f"  · 本页有 {len(caps)} 张图（{', '.join(str(n) for _, n in caps)}），"
                  f"默认取第一张；要指定请加 --fig N")
    cy = ws[i][1]
    cx0, cx1 = column_extent(ws, i)
    W, H = img.size
    x0, x1 = int((cx0 - 6) * S), int((cx1 + 6) * S)
    ybot = int((cy - 2) * S)
    px = img.load()

    def row_ink(y):
        return any(sum(px[x, y][:3]) < 735 for x in range(x0, x1))

    if strategy == "header":
        first_y = min(w[1] for w in ws)
        top = int((max(w[3] for w in ws if abs(w[1] - first_y) < 1) + 2) * S)
    else:                                    # gap
        y, gap, top, found = ybot, 0, None, False
        while y > 0:
            if row_ink(y):
                found = True; top = y; gap = 0
            else:
                gap += 1
                if found and gap > min_gap * S:
                    break
                if not found and gap > 30 * S:
                    print("  ! 图注上方 30pt 内找不到图，请用 --box"); return False
            y -= 1
        if top is None:
            print("  ! 没找到墨迹"); return False

    band = img.crop((x0, top, x1, ybot))
    bb = ink_bbox(band)
    if not bb:
        print("  ! 该区域是空白"); return False
    a, b, c, d = bb
    p = int(pad_pt * S)
    band.crop((max(0, a - p), max(0, b - p),
               min(band.size[0], c + p), min(band.size[1], d + p))).save(out)
    print(f"  ✓ {out.name}  (栏 {cx0:.0f}~{cx1:.0f}pt, {strategy}, {out.stat().st_size // 1024} KB)")
    return True


def crop_box(img: Image.Image, box_pt, out: Path):
    x0, y0, x1, y1 = [float(v) * S for v in box_pt]
    img.crop((int(x0), int(y0), int(x1), int(y1))).save(out)
    print(f"  ✓ {out.name}  (手动框, {out.stat().st_size // 1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--prefix", required=True, help="文件名前缀，一般用方法简称，如 TAPIR")
    ap.add_argument("--outdir", default="assets")
    ap.add_argument("--pages", default="", help="如 2,5,12 或 5:arch,2:teaser（页码:后缀）")
    ap.add_argument("--fig", type=int, default=None,
                    help="只裁该页的 Figure N（一页多图时必用；缺省取本页第一张）")
    ap.add_argument("--box", default="", help="手动模式，如 12:134,110,480,250；多条用 ; 分隔")
    ap.add_argument("--strategy", default="gap", choices=["gap", "header"])
    ap.add_argument("--min-gap", type=float, default=25.0, help="gap 策略判定'图与上方的分界'的空白高度(pt)")
    ap.add_argument("--tmp", default=None)
    args = ap.parse_args()

    pdf = Path(args.pdf).resolve()
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    tmp = Path(args.tmp) if args.tmp else outdir.parent / ".fig_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    if args.box:
        for spec in args.box.split(";"):
            page_s, coords = spec.split(":")
            page = int(page_s)
            crop_box(render(pdf, page, tmp), coords.split(","), outdir / f"{args.prefix}_p{page}.png")
        return

    if not args.pages:
        sys.exit("请用 --pages 指定页码，或用 --box 手动给框")

    for n, spec in enumerate(args.pages.split(","), 1):
        page = int(spec.split(":")[0])
        tag = spec.split(":")[1] if ":" in spec else f"fig{n}"
        out = outdir / f"{args.prefix}_{tag}.png"
        crop_auto(render(pdf, page, tmp), words(pdf, page), out,
                  strategy=args.strategy, min_gap=args.min_gap, fig_num=args.fig)


if __name__ == "__main__":
    main()
