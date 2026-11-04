"""图片并排渲染测试。"""
from pathlib import Path

from paperread.config import Config
from paperread.translate import render_fulltext


def _cfg() -> Config:
    return Config(vault_root=Path("."))


def test_image_grid_side_by_side():
    blocks = [
        {"id": "p1", "kind": "image", "text": "![](assets/fig1_a.png)", "page": 1},
        {"id": "p2", "kind": "image", "text": "![](assets/fig1_a_2.png)", "page": 1},
        {"id": "p3", "kind": "image", "text": "![](assets/fig1_a_3.png)", "page": 1},
        {"id": "p4", "kind": "para", "text": "Some text after images.", "page": 1},
    ]
    md = render_fulltext(_cfg(), "KEY00001", "Short", blocks, {}, {"title": "T"})
    assert "| ![](assets/fig1_a.png) | ![](assets/fig1_a_2.png) | ![](assets/fig1_a_3.png) |" in md
    assert md.count("![") == 3


def test_single_image_not_grid():
    blocks = [{"id": "p1", "kind": "image", "text": "![](assets/fig1_a.png)", "page": 1}]
    md = render_fulltext(_cfg(), "KEY00001", "Short", blocks, {}, {"title": "T"})
    assert "![](assets/fig1_a.png)" in md
    assert "|---|" not in md
