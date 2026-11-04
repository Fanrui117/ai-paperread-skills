"""离线演示：不需要 Zotero、不需要任何 API key。

用样例 blocks 生成一个临时 vault（含 full.md / note.md / assets），跑一遍
渲染 + 校验，展示完整产物长什么样。

用法（在仓库根目录）：
    .venv/bin/python examples/demo/run_demo.py            # 输出到临时目录
    .venv/bin/python examples/demo/run_demo.py ./demo-vault  # 输出到指定目录
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from PIL import Image

from paperread.config import Config
from paperread.plan import save_json
from paperread.translate import import_translations, translate_paper
from paperread.verify import render_report, verify_paper

HERE = Path(__file__).resolve().parent
KEY = "DEMO0001"
SHORT = "DemoPaper"
COLLECTION = "MyPapers"


def main() -> int:
    out = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="paperread-demo-"))
    out = out.resolve()
    cfg = Config(vault_root=out)
    cfg.ensure_dirs()

    paper_dir = cfg.library_root / COLLECTION / f"{SHORT}-{KEY}"
    (paper_dir / "assets").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (480, 180), (214, 226, 240)).save(paper_dir / "assets" / "fig1_demo.png")

    blocks = json.loads((HERE / "blocks.json").read_text(encoding="utf-8"))["blocks"]
    save_json(cfg.state_dir / "work" / KEY / "blocks.json", {"key": KEY, "short": SHORT, "blocks": blocks})
    save_json(
        cfg.library_json,
        {
            "papers": [
                {
                    "key": KEY,
                    "title": "Demo Paper: Tracking Any Point (synthetic example)",
                    "creators": ["Doe, Jane", "Roe, Richard"],
                    "date": "2026-01-01",
                    "publication": "DemoConf 2026",
                    "url": "https://example.com/demo",
                }
            ]
        },
    )
    save_json(
        cfg.state_json,
        {"version": 1, "papers": {KEY: {"short": SHORT, "dir": f"{COLLECTION}/{SHORT}-{KEY}"}}},
    )

    zh_file = out / "translate_zh.json"
    save_json(zh_file, json.loads((HERE / "translate_zh.json").read_text(encoding="utf-8")))
    n = import_translations(cfg, KEY, zh_file)
    fulltext = translate_paper(cfg, KEY, provider="agent", cache_only=True).fulltext

    note = f"""---
paper_key: {KEY}
title: "Demo Paper: Tracking Any Point (synthetic example)"
short: {SHORT}
collection: {COLLECTION}
tags: [demo, point-tracking]
status: draft
date: {date.today().isoformat()}
---

# {SHORT} — Demo Paper

> 这是一个离线演示：用 8 个样例块展示 PaperRead 的产物格式（中英对照 + 锚点 + 回链）。

![](assets/fig1_demo.png)

*演示图：论文流程示意图（占位）。*

| 项 | 值 |
|---|---|
| 任务 | 点跟踪（synthetic demo） |
| 指标 | AJ / $\\delta_{{avg}}^{{vis}}$ |

## 1. 核心结论

> 原文："Point tracking aims to follow any query point through a video."（[↓原文](full.md#^p2)）

作者把点跟踪形式化为「给定查询点预测轨迹 + 可见性」，损失函数是可见帧上的 L2 距离（[↓原文](full.md#^p3)）。

## 2. 结果

> 原文："Our method improves the average Jaccard (AJ) by 6.3 points over the baseline."（[↓原文](full.md#^p8)）

| Method | AJ | $\\delta_{{avg}}^{{vis}}$ |
|---|---|---|
| Baseline | 62.1 | 71.3 |
| Ours | **68.4** | **79.6** |

*表：样例结果表（与 full.md 中 `^p7` 对应）。*
"""
    (paper_dir / "note.md").write_text(note, encoding="utf-8")

    res = verify_paper(cfg, KEY)
    print(render_report(res))
    print(f"\n已导入 {n} 条译文")
    print(f"demo vault: {out}")
    print("可以用 Obsidian「打开文件夹作为库」查看，或直接读：")
    for name in ("full.md", "note.md"):
        print(f"  {paper_dir / name}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
