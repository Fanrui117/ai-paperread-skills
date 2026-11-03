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


def test_html_table_pipes_escaped():
    html = "<table><tr><td>a|b</td><td>c</td></tr></table>"
    md = html_table_to_markdown(html)
    assert "a\\|b" in md
