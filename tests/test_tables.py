"""表格升级：HTML 表格清洗。"""
from paperread.tables import clean_table_html


def test_clean_table_html_collapses_math_spaces():
    html = '<table><tr><td>$ { \\delta _ { \\mathrm { a v g } } } ^ { \\mathrm { v i s } }$</td></tr></table>'
    out = clean_table_html(html)
    assert "\\mathrm{avg}" in out
    assert "\\mathrm{vis}" in out
    assert "$ { " not in out


def test_clean_table_html_keeps_structure():
    html = '<table>\n  <tr><td rowspan="2">Method</td><td>AJ↑</td></tr>\n</table>'
    out = clean_table_html(html)
    assert 'rowspan="2"' in out
    assert "<table>" in out and "</table>" in out
