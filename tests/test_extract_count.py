"""图片计数：兼容 ![[assets/x]] 与 ![](assets/x)。"""
from paperread.extract import count_asset_images


def test_count_markdown_images():
    md = "![](assets/fig1_x.png)\n\n![](assets/eq1_p2.png)\n"
    assert count_asset_images(md) == 2


def test_count_wikilink_images():
    md = "![[assets/fig1_x.png]]\n![[assets/fig2_y.png|300]]\n"
    assert count_asset_images(md) == 2


def test_count_ignores_normal_links():
    md = "[↓原文](translated.md#^p1)\n![alt](https://example.com/a.png)\n"
    assert count_asset_images(md) == 0
