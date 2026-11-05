"""把 HTML 表格（MinerU 输出，含 rowspan/colspan）转成 Markdown 表格。

Obsidian 的 MathJax 不处理 HTML 表格里的 $...$，所以统一转成 Markdown 表格：
合并表头按列展开成「组名 + 指标名」，行内公式保留 $...$。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            attrs = dict(attrs)
            try:
                rowspan = max(1, int(attrs.get("rowspan", 1) or 1))
                colspan = max(1, int(attrs.get("colspan", 1) or 1))
            except ValueError:
                rowspan = colspan = 1
            self._cell = ["", rowspan, colspan]
        elif tag == "br" and self._cell is not None:
            self._cell[0] += " "

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[0] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", self._cell[0]).strip()
            self._row.append((text, self._cell[1], self._cell[2]))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _tighten_math(text: str) -> str:
    """压缩 LaTeX 里的空格：^{a v g} -> ^{avg}、_{x y} -> _{xy}。"""
    text = re.sub(r"\^\{([^}]*)\}", lambda m: "^{" + m.group(1).replace(" ", "") + "}", text)
    text = re.sub(r"_\{([^}]*)\}", lambda m: "_{" + m.group(1).replace(" ", "") + "}", text)
    return text


def html_table_to_markdown(html_str: str) -> str:
    parser = _TableParser()
    try:
        parser.feed(html_str)
    except Exception:  # noqa: BLE001
        return html_str
    rows = parser.rows
    if not rows:
        return html_str

    # 单行整行合并的说明文字（如 "Models evaluated at 256 × 256 resolution."）
    # 不应重复填满每一列：抽出来放到表格上方。
    def _note_row(row: list[tuple[str, int, int]]) -> str | None:
        if not row:
            return None
        text, _, colspan = row[0]
        if colspan >= 2 and len(text) > 30 and all(not t.strip() for t, _, _ in row[1:]):
            return _tighten_math(text)
        return None

    notes: list[str] = []
    data_rows: list[list[tuple[str, int, int]]] = []
    for row in rows:
        note = _note_row(row)
        if note:
            notes.append(note)
            continue
        data_rows.append(row)
    if data_rows:
        rows = data_rows

    grid: dict[tuple[int, int], str] = {}
    occupied: set[tuple[int, int]] = set()
    ncols = 0
    for r, row in enumerate(rows):
        c = 0
        for text, rowspan, colspan in row:
            while (r, c) in occupied:
                c += 1
            grid[(r, c)] = text
            for cc in range(c + 1, c + colspan):
                grid[(r, cc)] = text
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    occupied.add((rr, cc))
            c += colspan
            ncols = max(ncols, c)

    nrows = len(rows)
    has_span = any(rs > 1 or cs > 1 for row in rows for _, rs, cs in row)
    header_rows = 2 if has_span and nrows > 1 else 1

    # 去掉整列为空的尾巴（避免「列15」这种多余列）
    used = {c for (r, c), t in grid.items() if t.strip()}
    if used:
        ncols = min(ncols, max(used) + 1)

    def col_text(c: int) -> str:
        parts: list[str] = []
        for r in range(header_rows):
            t = grid.get((r, c), "").strip()
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        return _tighten_math(" ".join(parts))

    header = [col_text(c) or f"列{c + 1}" for c in range(ncols)]
    lines = ["| " + " | ".join(h.replace("|", "\\|") for h in header) + " |"]
    lines.append("|" + "---|" * ncols)
    for r in range(header_rows, nrows):
        cells = []
        for c in range(ncols):
            t = _tighten_math(grid.get((r, c), "").strip())
            cells.append(t.replace("|", "\\|") if t else "—")
        lines.append("| " + " | ".join(cells) + " |")
    table = "\n".join(lines)
    if notes:
        return "\n\n".join(notes) + "\n\n" + table
    return table


def looks_like_html_table(text: str) -> bool:
    return text.lstrip().startswith("<table")
