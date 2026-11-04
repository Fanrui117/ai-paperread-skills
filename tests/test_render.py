"""图片并排渲染测试（需要真实图片尺寸）。"""
from pathlib import Path

from PIL import Image

from paperread.config import Config
from paperread.plan import save_json
from paperread.translate import render_fulltext


def _setup(tmp_path: Path, sizes: list[tuple[int, int]]) -> tuple[Config, Path]:
    cfg = Config(vault_root=tmp_path / "vault")
    paper_dir = cfg.library_root / "MyPapers" / "Demo-DEMO0001"
    (paper_dir / "assets").mkdir(parents=True)
    for i, (w, h) in enumerate(sizes, 1):
        Image.new("RGB", (w, h), (200, 200, 200)).save(paper_dir / "assets" / f"fig1_a{'' if i == 1 else f'_{i}'}.png")
    save_json(cfg.state_json, {"version": 1, "papers": {"DEMO0001": {"short": "Demo", "dir": "MyPapers/Demo-DEMO0001"}}})
    return cfg, paper_dir


def _blocks(n: int) -> list[dict]:
    return [
        {"id": f"p{i + 1}", "kind": "image", "text": f"![](assets/fig1_a{'' if i == 0 else f'_{i + 1}'}.png)", "page": 1}
        for i in range(n)
    ]


def test_image_grid_side_by_side(tmp_path):
    cfg, _ = _setup(tmp_path, [(200, 150), (200, 150), (200, 150)])
    md = render_fulltext(cfg, "DEMO0001", "Demo", _blocks(3), {}, {"title": "T"})
    assert "| ![](assets/fig1_a.png) | ![](assets/fig1_a_2.png) | ![](assets/fig1_a_3.png) |" in md


def test_mismatched_sizes_not_gridded(tmp_path):
    cfg, _ = _setup(tmp_path, [(200, 150), (400, 100)])
    md = render_fulltext(cfg, "DEMO0001", "Demo", _blocks(2), {}, {"title": "T"})
    assert "|---|" not in md


def test_single_image_not_grid(tmp_path):
    cfg, _ = _setup(tmp_path, [(200, 150)])
    md = render_fulltext(cfg, "DEMO0001", "Demo", _blocks(1), {}, {"title": "T"})
    assert "![](assets/fig1_a.png)" in md
    assert "|---|" not in md
