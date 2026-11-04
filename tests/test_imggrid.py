"""图片排版（imggrid）测试。"""
from pathlib import Path

from PIL import Image

from paperread.config import Config
from paperread.imggrid import apply_layout, find_groups
from paperread.plan import save_json


def _blocks() -> list[dict]:
    return [
        {"id": "p1", "kind": "image", "text": "![](assets/fig1_a.png)", "page": 1},
        {"id": "p2", "kind": "image", "text": "![](assets/fig1_a_2.png)", "page": 1},
        {"id": "p3", "kind": "image", "text": "![](assets/fig2_b.png)", "page": 1},
        {"id": "p4", "kind": "para", "text": "text", "page": 1},
    ]


def test_find_groups():
    groups = find_groups(_blocks())
    assert len(groups) == 1
    assert groups[0].images == ["fig1_a.png", "fig1_a_2.png"]


def test_apply_grid(tmp_path):
    cfg = Config(vault_root=tmp_path / "vault")
    paper_dir = cfg.library_root / "MyPapers" / "Demo-DEMO0001"
    (paper_dir / "assets").mkdir(parents=True)
    for name in ("fig1_a.png", "fig1_a_2.png", "fig2_b.png"):
        Image.new("RGB", (200, 150), (220, 220, 220)).save(paper_dir / "assets" / name)
    save_json(cfg.state_dir / "work" / "DEMO0001" / "blocks.json", {"key": "DEMO0001", "short": "Demo", "blocks": _blocks()})
    save_json(cfg.state_json, {"version": 1, "papers": {"DEMO0001": {"short": "Demo", "dir": "MyPapers/Demo-DEMO0001"}}})

    layout = tmp_path / "layout.json"
    save_json(layout, {"g1": {"mode": "grid"}})
    n, fulltext = apply_layout(cfg, "DEMO0001", layout)
    assert n == 1
    text = fulltext.read_text(encoding="utf-8")
    assert "| ![](assets/fig1_a.png) | ![](assets/fig1_a_2.png) |" in text
    # fig2_b 不受影响
    assert "![](assets/fig2_b.png)" in text
