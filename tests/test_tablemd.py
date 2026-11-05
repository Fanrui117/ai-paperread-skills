"""表格 HTML -> Markdown 转换测试。"""
from paperread.tablemd import html_table_to_markdown


def test_html_table_with_merged_header():
    html = (
        '<table><tr><td rowspan="2">Method</td><td rowspan="2">Train</td>'
        '<td colspan="3">Kinetics</td><td colspan="2">RGB-S</td><td>Mean</td></tr>'
        "<tr><td>AJ</td><td>d</td><td>OA</td><td>AJ</td><td>d</td><td>x</td></tr>"
        "<tr><td>PIPs</td><td>PO</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td></tr></table>"
    )
    md = html_table_to_markdown(html)
    header = md.splitlines()[0]
    assert "Kinetics AJ" in header
    assert "Kinetics d" in header
    assert "RGB-S AJ" in header
    assert "PIPs" in md


def test_html_table_keeps_inline_math():
    html = '<table><tr><td>$x^2$</td><td>1</td></tr></table>'
    md = html_table_to_markdown(html)
    assert "$x^2$" in md


def test_full_width_note_moved_out():
    html = (
        '<table><tr><td colspan="2">Models evaluated at 256 x 256 resolution.</td></tr>'
        "<tr><td>AJ</td><td>OA</td></tr>"
        "<tr><td>1</td><td>2</td></tr></table>"
    )
    md = html_table_to_markdown(html)
    lines = md.splitlines()
    assert lines[0].startswith("Models evaluated")
    header = next(line for line in lines if line.startswith("|"))
    assert "Models" not in header
    assert "列" not in header  # 不再出现多余的「列15」


def test_note_row_with_trailing_empty_cells():
    html = (
        '<table><tr><td colspan="2">Group A</td></tr>'
        '<tr><td colspan="3">Models evaluated at 256 x 256 resolution.</td><td></td><td></td><td></td></tr>'
        "<tr><td>AJ</td><td>OA</td></tr>"
        "<tr><td>1</td><td>2</td></tr></table>"
    )
    md = html_table_to_markdown(html)
    assert "Models evaluated" in md.splitlines()[0]
    # 说明文字只出现一次
    assert md.count("Models evaluated") == 1


def test_math_spaces_tightened():
    html = '<table><tr><td>$\\delta^{a v g}\\uparrow$</td><td>1</td></tr></table>'
    md = html_table_to_markdown(html)
    assert "^{avg}" in md


def test_html_table_pipes_escaped():
    html = "<table><tr><td>a|b</td><td>c</td></tr></table>"
    md = html_table_to_markdown(html)
    assert "a\\|b" in md

