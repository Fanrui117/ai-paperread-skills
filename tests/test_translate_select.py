"""翻译块筛选与图注正则。"""
from paperread.extract import CAPTION_RE, TABLE_CAPTION_RE
from paperread.translate import _select_items


def test_caption_regex_variants():
    assert CAPTION_RE.match("Figure 1: The problem")
    assert CAPTION_RE.match("Figure 2 | Architecture")
    assert CAPTION_RE.match("Fig. 3: Something")
    assert not CAPTION_RE.match("Figure 1 shows the results")
    assert TABLE_CAPTION_RE.match("Table 2 | Comparison")
    assert TABLE_CAPTION_RE.match("Table 3: Results")
    assert not TABLE_CAPTION_RE.match("Table 2 shows the results")


def test_select_items_skips_front_and_refs():
    blocks = [
        {"id": "p1", "kind": "heading", "text": "# Title"},
        {"id": "p2", "kind": "para", "text": "Author affiliation information goes here."},
        {"id": "p3", "kind": "heading", "text": "## Abstract"},
        {"id": "p4", "kind": "para", "text": "This is a real abstract paragraph with enough words."},
        {"id": "p5", "kind": "heading", "text": "## References"},
        {"id": "p6", "kind": "para", "text": "[1] Some reference entry with authors and title."},
        {"id": "p7", "kind": "heading", "text": "## A Appendix"},
        {"id": "p8", "kind": "para", "text": "Appendix paragraph that should be translated too."},
    ]
    ids = [bid for bid, _ in _select_items(blocks)]
    assert "p2" not in ids          # Abstract 之前跳过
    assert "p4" in ids
    assert "p6" not in ids          # 参考文献跳过
    assert "p8" in ids              # 附录恢复


def test_select_items_skips_short_and_symbol_heavy():
    blocks = [
        {"id": "p1", "kind": "heading", "text": "## 1 Introduction"},
        {"id": "p2", "kind": "para", "text": "12345"},
        {"id": "p3", "kind": "para", "text": "$$ \\sum_i x_i = y $$"},
        {"id": "p4", "kind": "para", "text": "A normal English paragraph with plenty of letters."},
    ]
    ids = [bid for bid, _ in _select_items(blocks)]
    # 标题要翻译（渲染成「## 1 Introduction（引言）」），短段/公式段跳过
    assert ids == ["p1", "p4"]


def test_select_items_roman_numeral_heading():
    blocks = [
        {"id": "p1", "kind": "heading", "text": "# PAPER TITLE"},
        {"id": "p2", "kind": "para", "text": "Authors and affiliations."},
        {"id": "p3", "kind": "heading", "text": "## I. INTRODUCTION"},
        {"id": "p4", "kind": "para", "text": "Intro paragraph long enough to be translated."},
        {"id": "p5", "kind": "heading", "text": "## II. RELATED WORK"},
        {"id": "p6", "kind": "para", "text": "Related work paragraph long enough to translate."},
    ]
    ids = [bid for bid, _ in _select_items(blocks)]
    assert "p2" not in ids          # 封面跳过
    assert "p3" in ids and "p4" in ids
    assert "p5" in ids and "p6" in ids


def test_select_items_plain_text_references():
    blocks = [
        {"id": "p1", "kind": "heading", "text": "## 1 Introduction"},
        {"id": "p2", "kind": "para", "text": "Intro paragraph long enough to be translated."},
        {"id": "p3", "kind": "para", "text": "REFERENCES"},
        {"id": "p4", "kind": "para", "text": "[1] Some reference entry with authors and title."},
        {"id": "p5", "kind": "heading", "text": "## A Appendix"},
        {"id": "p6", "kind": "para", "text": "Appendix paragraph that should be translated too."},
    ]
    ids = [bid for bid, _ in _select_items(blocks)]
    assert "p2" in ids
    assert "p3" not in ids and "p4" not in ids   # 纯文本 REFERENCES 也当参考文献起点
    assert "p6" in ids
