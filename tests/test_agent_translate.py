"""agent 翻译工作流：导出任务 / 导入译文 / 渲染中英对照。"""
from paperread.config import Config
from paperread.plan import save_json
from paperread.translate import emit_translation_tasks, import_translations, render_fulltext

BLOCKS = [
    {"id": "p1", "kind": "heading", "text": "## Abstract", "page": 1},
    {"id": "p2", "kind": "para", "text": "This is a paragraph long enough to be translated properly.", "page": 1},
    {"id": "p3", "kind": "image", "text": "![[assets/img001.jpg]]", "page": 1},
]


def setup(tmp_path):
    cfg = Config(vault_root=tmp_path / "vault")
    work = cfg.state_dir / "work" / "AAAA1111"
    work.mkdir(parents=True)
    save_json(work / "blocks.json", {"key": "AAAA1111", "short": "Alpha", "blocks": BLOCKS})
    return cfg


def test_emit_and_import_roundtrip(tmp_path):
    cfg = setup(tmp_path)
    paths = emit_translation_tasks(cfg, "AAAA1111")
    assert paths and paths[0].is_file()
    import json

    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    # Abstract 之前的封面被过滤；Abstract 标题与正文段落都要翻译
    assert [b["id"] for b in payload["blocks"]] == ["p1", "p2"]

    zh_file = tmp_path / "zh.json"
    save_json(zh_file, {"p2": "这是一段足够长、可以正常翻译的段落。"})
    n = import_translations(cfg, "AAAA1111", zh_file)
    assert n == 1

    md = render_fulltext(cfg, "AAAA1111", "Alpha", BLOCKS, {"p2": "这是一段足够长、可以正常翻译的段落。"}, {"title": "Alpha"})
    assert "^p2" in md
    assert "这是一段足够长、可以正常翻译的段落。" in md
    # 图片统一渲染为相对 markdown（Obsidian 子目录里 ![[assets/x]] 会找不到）
    assert "![](assets/img001.jpg)" in md


def test_import_rejects_bad_payload(tmp_path):
    cfg = setup(tmp_path)
    bad = tmp_path / "bad.json"
    save_json(bad, ["not", "a", "dict"])
    try:
        import_translations(cfg, "AAAA1111", bad)
    except RuntimeError as exc:
        assert "格式不对" in str(exc)
    else:
        raise AssertionError("应当报错")
