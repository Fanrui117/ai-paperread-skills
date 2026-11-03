"""目录规划：忽略规则、状态判定。"""
from pathlib import Path

from paperread.config import Config
from paperread.plan import build_plan
from paperread.zotero import Attachment, Collection, Paper, ZoteroLibrary


def make_lib(tmp_path: Path) -> ZoteroLibrary:
    lib = ZoteroLibrary(data_dir=tmp_path)
    lib.collections[1] = Collection(1, "COLL1", "6DPose", None, ("6DPose",))
    lib.collections[2] = Collection(2, "COLL2", "Zdraft", None, ("Zdraft",))
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    lib.papers = [
        Paper(
            item_id=1, key="AAAA1111", item_type="preprint", title="Alpha: A Paper",
            collection_ids=[1],
            attachments=[Attachment(10, "ATT1", "application/pdf", pdf, pdf.stat().st_size, pdf.stat().st_mtime)],
        ),
        Paper(item_id=2, key="BBBB2222", item_type="preprint", title="Draft Thing", collection_ids=[2]),
        Paper(item_id=3, key="CCCC3333", item_type="preprint", title="No PDF Paper", collection_ids=[1]),
    ]
    return lib


def make_cfg(tmp_path: Path) -> Config:
    return Config(vault_root=tmp_path / "vault")


def test_plan_status_and_ignore(tmp_path):
    lib = make_lib(tmp_path)
    cfg = make_cfg(tmp_path)
    cfg.ignore_collections = ["Zdraft"]
    plan = build_plan(lib, cfg, state={"version": 1, "papers": {}})
    keys = {e.key for e in plan.entries}
    assert "AAAA1111" in keys          # 正常论文
    assert "BBBB2222" not in keys      # 草稿集合被忽略
    assert "CCCC3333" in keys          # 无 PDF 仍列出（提示）
    status = {e.key: e.status for e in plan.entries}
    assert status["AAAA1111"] == "new"
    assert status["CCCC3333"] == "no_pdf"


def test_plan_ignore_items(tmp_path):
    lib = make_lib(tmp_path)
    cfg = make_cfg(tmp_path)
    cfg.ignore_items = ["AAAA1111"]
    plan = build_plan(lib, cfg, state={"version": 1, "papers": {}})
    assert "AAAA1111" not in {e.key for e in plan.entries}


def test_plan_exists_and_stale(tmp_path):
    lib = make_lib(tmp_path)
    cfg = make_cfg(tmp_path)
    entry_target = cfg.library_root / "6DPose" / "Alpha-AAAA1111"
    entry_target.mkdir(parents=True)
    # 无状态记录 -> exists
    plan = build_plan(lib, cfg, state={"version": 1, "papers": {}})
    e = next(x for x in plan.entries if x.key == "AAAA1111")
    assert e.status == "exists"
    # 状态里 PDF 哈希不同 -> stale
    plan = build_plan(lib, cfg, state={"version": 1, "papers": {"AAAA1111": {"pdf_sha256": "deadbeef"}}})
    e = next(x for x in plan.entries if x.key == "AAAA1111")
    assert e.status == "stale"
