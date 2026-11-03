"""公式升级与行内公式修正工作流。"""
import json

from paperread.config import Config
from paperread.formulas import emit_formula_tasks, import_formulas
from paperread.mathfix import emit_mathfix_tasks, import_mathfix
from paperread.plan import save_json
from paperread.translate import _cache_path, _sha


def setup(tmp_path):
    cfg = Config(vault_root=tmp_path / "vault")
    work = cfg.state_dir / "work" / "AAAA1111"
    work.mkdir(parents=True)
    paper_dir = cfg.library_root / "6DPose" / "Alpha-AAAA1111"
    (paper_dir / "assets").mkdir(parents=True)
    (paper_dir / "assets" / "eq1_p1.png").write_bytes(b"png")
    blocks = [
        {"id": "p1", "kind": "image", "text": "![[assets/eq1_p1.png]]", "page": 1},
        {"id": "p2", "kind": "para", "text": "Some paragraph long enough to translate properly.", "page": 1},
    ]
    save_json(work / "blocks.json", {"key": "AAAA1111", "short": "Alpha", "blocks": blocks})
    save_json(
        cfg.state_json,
        {"version": 1, "papers": {"AAAA1111": {"short": "Alpha", "dir": "6DPose/Alpha-AAAA1111"}}},
    )
    return cfg


def test_formula_emit_and_import(tmp_path):
    cfg = setup(tmp_path)
    task_path = emit_formula_tasks(cfg, "AAAA1111")
    items = json.loads(task_path.read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in items] == ["p1"]
    assert items[0]["image"] == "assets/eq1_p1.png"

    latex_file = tmp_path / "latex.json"
    save_json(latex_file, {"p1": "x^2 + y^2 = z^2"})
    n, fulltext = import_formulas(cfg, "AAAA1111", latex_file)
    assert n == 1
    text = fulltext.read_text(encoding="utf-8")
    assert "$$" in text and "x^2 + y^2 = z^2" in text


def test_mathfix_emit_and_import(tmp_path):
    cfg = setup(tmp_path)
    source = "Some paragraph long enough to translate properly."
    save_json(_cache_path(cfg, "AAAA1111", "agent"), {_sha(source): "其中 _x_0 表示初始值。"})
    paths = emit_mathfix_tasks(cfg, "AAAA1111")
    items = json.loads(paths[0].read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in items] == ["p2"]

    fix_file = tmp_path / "fix.json"
    save_json(fix_file, {"p2": "其中 $x_0$ 表示初始值。"})
    n, fulltext = import_mathfix(cfg, "AAAA1111", fix_file)
    assert n == 1
    assert "$x_0$" in fulltext.read_text(encoding="utf-8")
