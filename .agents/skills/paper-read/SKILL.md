---
name: paper-read
description: 用 paperread CLI 把 Zotero 论文转成 Obsidian 论文笔记库（扫描→规划→抽取→翻译→精读笔记→校验）。当用户要处理/阅读/同步某篇论文、生成中英对照全文或精读笔记时使用。
whenToUse: 用户提到 Zotero 论文、Obsidian 论文笔记、全文翻译、精读笔记、论文库同步时。
---

# PaperRead：Zotero → Obsidian 论文流水线

## 环境与边界

- 项目根：本仓库根目录（所有命令在此执行）
  - Windows：`.venv\Scripts\paperread.exe`；macOS/Linux：`.venv/bin/paperread`
- 配置：`config/settings.toml`（vault 路径、Zotero 路径、API key 都在这里；没有就 `paperread init`）
- vault 写边界：只写 `[vault].root` 指定的目录（默认 `~/PaperNotes`），不要写到 vault 之外
- **Zotero 只读**：不要修改 Zotero 数据目录（`[zotero].data_dir`）下任何文件

## 目录约定

```
<vault>/<集合>/<简称>-<ZoteroKey>/
├── note.md       # 精读笔记
├── summary.md    # 一页速览
├── full.md       # 中英对照全文（段末 ^pN 锚点）
├── src.md        # 英文源（翻译输入）
└── assets/       # 图片（figN_*.png / eqN_*.png / imgNNN.jpg）
```

## 命令

| 命令 | 作用 |
|---|---|
| `paperread init` | 生成 `config/settings.toml` 与 vault 目录 |
| `paperread doctor` | 环境自检（配置 / Zotero / vault / key / 依赖） |
| `paperread scan` | 只读扫描 Zotero → `vault/.paperread/library.json` + 摘要 |
| `paperread find <query>` | 只读查找：key / 标题片段 / DOI / 作者 |
| `paperread add <query> [--run]` | 单篇：按 key/标题/DOI 建目录（不扫全库），`--run` 直接跑流水线 |
| `paperread ingest <arxiv\|doi\|url> [--collection] [--run]` | 写入 Zotero（Connector API，可选集合），再处理进 vault |
| `paperread plan` | 目录规划预览；`--apply` 创建目录并写 state |
| `paperread extract <key> [--force] [--backend mineru]` | PDF → `src.md` + `assets/` + blocks.json；`--backend mineru` 用云端 API（LaTeX 公式 + HTML 表格） |
| `paperread translate <key> [--dry-run] [--provider baidu\|agent] [--main-only]` | 生成中英对照 `full.md` |
| `paperread translate <key> --emit-tasks` | 导出待翻译块（`work/<key>/translate_tasks/partNN.json`） |
| `paperread translate <key> --provider agent --import-json <file>` | 导入 agent 译文 `{block_id: 中文}` 并渲染全文（不调用外部 API） |
| `paperread packet <key>` | 写作包（大纲+锚点+图清单+摘要）→ `vault/.paperread/packets/<key>.md` |
| `paperread formulas <key> --emit-tasks` | 导出待识别公式（`assets/eq*.png` + 短宽条图 → LaTeX 任务） |
| `paperread formulas <key> --import-json <f>` | 导入 `{block_id: latex}`，原地替换公式并重渲染 `full.md`（保留锚点） |
| `paperread tables <key>` | 用 MinerU 的 HTML 表格升级管道表格（保留 rowspan/colspan 与锚点），自动转成 Markdown 表格（Obsidian 表格内 `$...$` 才能渲染） |
| `paperread tables <key> --import-json <f>` | 手工/agent 提供 `{block_id: 表格文本}` 并重渲染 |
| `paperread fixsrc <key> --import-json <f>` | 修正被 PDF 抽取破坏的源段落：`{block_id: {src, zh}}`，自动重映射译文缓存 |
| `paperread page <key> <page>` | 把 PDF 某页渲染成 PNG（`work/<key>/pages/pNNN.png`），供 agent 核对公式/表格 |
| `paperread verify <key> \| --all` | 校验链接/锚点/图片/frontmatter/引文 |
| `paperread audit` | 审计库：可用 / 缺 PDF / 孤儿 / 已忽略 |
| `paperread run <key> \| --collection X` | extract → translate → packet 一条龙 |
| `paperread index` | 生成 vault 根目录 `_索引.md` |
| `paperread status` | vault 覆盖率（F/f/N/S） |

## 单篇标准流程

1. **定位论文**：`paperread find <关键词/DOI/key>`（只读）；找不到时让用户先入库（浏览器插件或 `paperread ingest`）。
2. **建目录**：`paperread add <key>`（只建这一篇；`--run` 可一步跑 extract→translate→packet）。
3. **抽取**：`paperread extract <key>`；用 read_image 抽查 `assets/fig1_*` 是否切对。
4. **翻译（agent 工作流，推荐）**：
   - `paperread translate <key> --emit-tasks` 导出待译块；
   - 逐块翻译成中文，写 `{"<block_id>": "<中文>"}` JSON（标题不带 `##` 前缀，公式/术语原样保留）；
   - `paperread translate <key> --provider agent --import-json <file>` 渲染 `full.md`。
5. **写笔记**：读 `templates/note-template.md` 与写作包，写 `note.md`；再写 `summary.md`。
6. **校验**：`paperread verify <key>`，全绿才交付。

> 全库场景（首次建库/批量）：`paperread scan` → `paperread plan --apply` → `paperread status`。

## 笔记硬规则

- 出处用相对链接：`[↓原文](full.md#^pN)`；图片 `![](assets/figN_xxx.png)`；跨论文 `[PaperB](../PaperB-YYYYYYYY/note.md)`。
- 公式统一用 Markdown 数学语法：**行内 `$...$`**、**行间 `$$...$$`**（不要用图片或 `_x_` 模拟上下标）。
- 不编造数字；原文没有的不写；不确定处标注「原文未明确」。
- 不覆盖已有笔记：写前检查文件是否存在，存在则先问用户。
- 中文为主，专业术语保留英文原词。

## 公式处理

- 抽取后的公式图：`paperread formulas <key> --emit-tasks` → agent 看图写 LaTeX → `--import-json` 原地替换成 `$$...$$`（保留锚点）。
  - 候选包括 `eq*.png`，也包括**短宽条图**（高≤200、宽高比≥2.2）——抽取器有时把公式裁成 `fig*.png`，别漏。
  - **同一张图里的多行公式组**（如式 (3)(4) 在一张图）用 `\begin{aligned}...\end{aligned}` 保留为一个 `$$` 块，不能因为行多就跳过。
- 中文译文里的行内数学片段：`paperread mathfix <key> --emit-tasks` → agent 把片段包成 `$...$` → `--import-json` 更新译文并重渲染。

## 常见故障

- `Local API is not enabled` → 我们走 sqlite 只读副本，不影响。
- Zotero 运行中库被锁 → 工具会先拷贝副本再读，失败重试一次。
- Obsidian 里图片显示「找不到」→ 检查是否用了 `![](assets/x)` 相对路径（`![[assets/x]]` 是 vault 根相对，会找不到）。
- 换解析后端（pymupdf ↔ mineru）会改变段落锚点 → 已有笔记链接失效，需重跑 translate 并重生成笔记。
