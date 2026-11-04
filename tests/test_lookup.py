"""单篇查找 / 入库解析 / 规划过滤。"""
from pathlib import Path

from paperread.config import Config
from paperread.ingest import parse_identifier, resolve_one, search_papers
from paperread.plan import build_plan, load_state, save_json
from paperread.zotero import Paper, ZoteroLibrary


def _lib() -> ZoteroLibrary:
    return ZoteroLibrary(
        data_dir=Path("."),
        papers=[
            Paper(
                item_id=1,
                key="AAAA1111",
                item_type="journalArticle",
                title="Point Tracking with Pixels",
                creators=["Doe, Jane"],
                date="2024",
                doi="10.1000/abc",
            ),
            Paper(
                item_id=2,
                key="BBBB2222",
                item_type="journalArticle",
                title="Language Models Are Good",
                creators=["Roe, Richard"],
                date="2025",
                doi="10.2000/xyz",
            ),
            Paper(
                item_id=3,
                key="CCCC3333",
                item_type="journalArticle",
                title="Point Tracking Revisited",
                creators=["Poe, Ann"],
                date="2023",
            ),
        ],
    )


def test_parse_identifier():
    assert parse_identifier("2401.12345") == ("arxiv", "2401.12345")
    assert parse_identifier("arXiv:2401.12345v2") == ("arxiv", "2401.12345")
    assert parse_identifier("https://arxiv.org/abs/2401.12345") == ("arxiv", "2401.12345")
    assert parse_identifier("10.1000/abc") == ("doi", "10.1000/abc")
    assert parse_identifier("https://example.com/paper") == ("url", "https://example.com/paper")
    assert parse_identifier("some paper title") == ("query", "some paper title")


def test_search_by_key_title_doi_author():
    lib = _lib()
    assert [p.key for p in search_papers(lib, "AAAA1111")] == ["AAAA1111"]
    assert [p.key for p in search_papers(lib, "point tracking")] == ["CCCC3333", "AAAA1111"]
    assert [p.key for p in search_papers(lib, "10.2000/xyz")] == ["BBBB2222"]
    assert [p.key for p in search_papers(lib, "Doe")] == ["AAAA1111"]
    assert search_papers(lib, "nonexistent") == []


def test_resolve_one_unique_and_ambiguous():
    lib = _lib()
    paper, matches = resolve_one(lib, "BBBB2222")
    assert paper is not None and paper.key == "BBBB2222" and matches == []
    paper, matches = resolve_one(lib, "point tracking")
    assert paper is None and len(matches) == 2


def test_plan_filter_by_keys(tmp_path):
    cfg = Config(vault_root=tmp_path / "vault")
    save_json(cfg.state_json, {"version": 1, "papers": {}})
    plan = build_plan(_lib(), cfg, load_state(cfg), keys={"BBBB2222"})
    assert [e.key for e in plan.entries] == ["BBBB2222"]
