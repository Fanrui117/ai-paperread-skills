"""表格文本清洗（抽取噪声）。"""
from paperread.translate import _clean_table_md


def test_clean_table_numbers():
    text = "|TAPNet [11]||33_._0|48_._6|\n|---|---|\n"
    out = _clean_table_md(text)
    assert "33.0" in out and "48.6" in out
    assert "_._" not in out


def test_clean_table_math_markers():
    text = "|||AJ_↑_|_δ_<sup>_avg _</sup>_↑_|6_×_256r|"
    out = _clean_table_md(text)
    assert "AJ↑" in out
    assert "6×256r" in out
    assert "_δ_" not in out


def test_clean_table_keeps_plain_text():
    text = "| Method | Train |\n|---|---|\n| PIPs | PO |"
    out = _clean_table_md(text)
    assert "PIPs" in out and "PO" in out
