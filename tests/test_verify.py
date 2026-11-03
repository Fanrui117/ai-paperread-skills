"""verify：锚点 / 链接 / 图片 / 引文检查（v2 文件名与相对 markdown 链接）。"""
from pathlib import Path

from paperread.config import Config
from paperread.plan import save_json
from paperread.verify import verify_paper


def setup_paper(tmp_path: Path) -> Config:
    cfg = Config(vault_root=tmp_path / "vault")
    paper_dir = cfg.library_root / "6DPose" / "Alpha-AAAA1111"
    (paper_dir / "assets").mkdir(parents=True)
    (paper_dir / "assets" / "fig1_x.png").write_bytes(b"png")
    fulltext = (
        "---\npaper_key: \"AAAA1111\"\ntitle: \"Alpha\"\n---\n\n# Alpha\n\n"
        "This is the first paragraph. ^p7\n\n"
        "This is the second paragraph. ^p8\n"
    )
    (paper_dir / "full.md").write_text(fulltext, encoding="utf-8")
    save_json(cfg.state_json, {"version": 1, "papers": {"AAAA1111": {"short": "Alpha", "dir": "6DPose/Alpha-AAAA1111"}}})
    return cfg


def test_verify_all_green(tmp_path):
    cfg = setup_paper(tmp_path)
    note = cfg.library_root / "6DPose" / "Alpha-AAAA1111" / "note.md"
    note.write_text(
        "---\npaper_key: AAAA1111\ntitle: Alpha\n---\n\n"
        "![](assets/fig1_x.png)\n\n"
        "> 原文：\"This is the first paragraph.\"（[↓原文](full.md#^p7)）\n\n"
        "另一处引用（[↓原文](full.md#^p8)）。\n",
        encoding="utf-8",
    )
    res = verify_paper(cfg, "AAAA1111")
    assert res.ok, [i.msg for i in res.issues]
    assert res.links_total == 2 and res.links_ok == 2
    assert res.embeds_total == 1 and res.embeds_ok == 1
    assert res.quotes_total == 1 and res.quotes_ok == 1


def test_verify_bad_anchor_and_missing_image(tmp_path):
    cfg = setup_paper(tmp_path)
    note = cfg.library_root / "6DPose" / "Alpha-AAAA1111" / "note.md"
    note.write_text(
        "---\npaper_key: AAAA1111\ntitle: Alpha\n---\n\n"
        "坏锚点（[↓原文](full.md#^p999)）\n\n"
        "缺图 ![](assets/nope.png)\n",
        encoding="utf-8",
    )
    res = verify_paper(cfg, "AAAA1111")
    assert not res.ok
    msgs = " ".join(i.msg for i in res.issues)
    assert "锚点不存在" in msgs
    assert "图片不存在" in msgs


def test_verify_legacy_wikilink(tmp_path):
    """兼容旧格式 [[<简称>_fulltext#^p7|↓原文]]。"""
    cfg = setup_paper(tmp_path)
    paper_dir = cfg.library_root / "6DPose" / "Alpha-AAAA1111"
    (paper_dir / "Alpha_fulltext.md").write_text("# Alpha\n\npara ^p7\n", encoding="utf-8")
    note = paper_dir / "note.md"
    note.write_text(
        "---\npaper_key: AAAA1111\ntitle: Alpha\n---\n\n"
        "旧链接（[[Alpha_fulltext#^p7|↓原文]]）\n",
        encoding="utf-8",
    )
    res = verify_paper(cfg, "AAAA1111")
    assert res.links_total == 1 and res.links_ok == 1, [i.msg for i in res.issues]
