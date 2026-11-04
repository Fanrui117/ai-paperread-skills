"""笔记同步：公式图 -> LaTeX。"""
from pathlib import Path

from paperread.config import Config
from paperread.notesync import sync_notes
from paperread.plan import save_json


def _setup(tmp_path: Path) -> Config:
    cfg = Config(vault_root=tmp_path / "vault")
    paper_dir = cfg.library_root / "MyPapers" / "Demo-DEMO0001"
    (paper_dir / "assets").mkdir(parents=True)
    (paper_dir / "assets" / "eq1_p5.png").write_bytes(b"png")
    (paper_dir / "assets" / "fig1_x.png").write_bytes(b"png")
    save_json(
        cfg.state_dir / "work" / "DEMO0001" / "blocks.json",
        {
            "key": "DEMO0001",
            "short": "Demo",
            "blocks": [
                {"id": "p1", "kind": "equation", "text": "$$\nh_t = a_t \\odot h_{t-1} + x_t\n$$", "latex": "h_t = a_t \\odot h_{t-1} + x_t", "orig_image": "eq1_p5.png", "page": 5},
                {"id": "p2", "kind": "para", "text": "text", "page": 5},
            ],
        },
    )
    save_json(cfg.state_json, {"version": 1, "papers": {"DEMO0001": {"short": "Demo", "dir": "MyPapers/Demo-DEMO0001"}}})
    return cfg


def test_notesync_replaces_equation_image(tmp_path):
    cfg = _setup(tmp_path)
    note = cfg.library_root / "MyPapers" / "Demo-DEMO0001" / "note.md"
    note.write_text(
        "# Demo\n\n![](assets/eq1_p5.png)\n\n![](assets/fig1_x.png)\n",
        encoding="utf-8",
    )
    n, kept, files = sync_notes(cfg, "DEMO0001", apply=True)
    assert n == 1 and kept == 1 and files == ["note.md"]
    text = note.read_text(encoding="utf-8")
    assert "$$" in text and "h_t = a_t" in text
    assert "fig1_x.png" in text


def test_notesync_dry_run_does_not_write(tmp_path):
    cfg = _setup(tmp_path)
    note = cfg.library_root / "MyPapers" / "Demo-DEMO0001" / "note.md"
    before = "# Demo\n\n![[assets/eq1_p5.png]]\n"
    note.write_text(before, encoding="utf-8")
    n, kept, files = sync_notes(cfg, "DEMO0001", apply=False)
    assert n == 1 and kept == 0 and files == []
    assert note.read_text(encoding="utf-8") == before
