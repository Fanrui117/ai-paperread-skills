# PaperRead 方案 v0.2

> Zotero 是唯一事实来源，Obsidian 是派生产物。一次运行：扫描 Zotero 库 → 按集合结构在 vault 建目录 →
> 每篇论文产出 `assets/` 配图、中英对照全文、AI 精读笔记，且笔记里的引用能精确链回全文段落。

## 0. 决策记录（v0.2，2026-11-03）

| 项 | 决定 | 备注 |
|---|---|---|
| Demo vault | `<vault>`（新建） | 与代码仓库解耦；Obsidian「打开文件夹作为库」即可；state 放在 vault 内随库迁移 |
| 文件夹命名 | `<AI简称>-<ZoteroKey>`，如 `SomePaper-XXXXXXXX` | 可读 + 稳定；简称可人工覆写 |
| 全文翻译 | DeepSeek API 批处理（术语表 + 缓存），段级 EN→ZH 交替 | 需要 API key（放 `config/settings.toml`，gitignore） |
| 精读笔记 | DSH agent 按 skill 模板撰写 | 引用必须带全文锚点链接；质量对齐 `templates/note-template.md` |
| Zotero | MVP 只读；本地 API 优先，sqlite 只读副本回退 | 本地 API 未开时自动回退，不阻塞 |
| 形态 | Python CLI（uv）+ DSH skills | MCP 留 P3；当前预设未挂载 dsh-mcp-client |
| 验收样本 | TAP-Vid（`XXXXXXXX`） | 与现有样例逐节对照 |

## 1. 目标与非目标

**目标（P2 前）**
1. `scan`：只读枚举 Zotero 集合树 / 条目 / 附件，输出库清单与增量 diff。
2. `plan`：给出 vault 目录规划（集合路径 + 简称 + 目标目录）与状态（new/stale/exists/orphan/no_pdf），可预览。
3. `run`：单篇论文 → `assets/`、`<简称>.fulltext.md`（中英交替 + 锚点）、`<简称>.md`（精读笔记）。
4. `verify`：链接 / 锚点 / 图片 / frontmatter / 数字引用可解析性校验。
5. 批量 10 篇（MyPapers 5 + MyPapers2 5），支持增量（新论文、PDF 更新、条目移动/改名）。

**非目标（P3 再做）**：Zotero 写回（PDF 高亮/深链）、外部四源检索、问答/综述、MCP server、Dataview 看板、多用户并发。

## 2. 目录与产物

```
<vault>\                       # vault root（可配置）
├── MyPapers\                          # 1:1 镜像 Zotero 集合（只镜像含论文的集合）
│   ├── SomePaper-XXXXXXXX\            # <AI简称>-<ZoteroKey>
│   │   ├── note.md                  # 精读笔记（对齐样例格式）
│   │   ├── summary.md               # 一页速览
│   │   ├── full.md                  # 中英对照全文（^pN 锚点）
│   │   ├── src.md                   # 英文源（翻译输入）
│   │   └── assets\
│   │       ├── fig1_task_overview.png
│   │       ├── fig3_four_datasets.png
│   │       └── eq3_loss.png
│   ├── TAPIR-...\  PIPs-...\  CoTracker3-...\  TAPNext++-...\
├── MyPapers2\
│   ├── DeepSeek-V2-...\ ... Orca-...
└── .paperread\
    ├── library.json                 # scan 结果缓存
    ├── plan.json                    # 最近一次规划
    └── state.json                   # 增量状态（key → 目录/哈希/产物时间）
```

命名约定：**文件夹带 key（稳定、防重名），文件用固定短名（`note.md` / `summary.md` / `full.md` / `src.md`），assets 内文件不再带论文前缀**（已在论文目录内）。
链接约定：笔记里用**相对 markdown 链接**（`[↓原文](full.md#^pN)`、`![](assets/x.png)`、跨论文 `[PIPs](../ThirdPaper-ZZZZZZZZ/note.md)`），因为 Obsidian 的 `![[assets/x]]` 是 vault 根相对，在子目录里会找不到。

- 集合名做文件名清洗（`<>:"/\|?*` → `-`），保留中文。
- 一篇论文属于多个集合时：主目录放最浅/字典序最小的集合；其余集合记在 state，P2 可选生成 redirect 笔记。
- 只镜像「含 PDF 论文」的集合路径；`--all-collections` 可强制建空集合目录。

## 3. 文件规范

### 3.1 `full.md`（中英对照全文）

```markdown
---
paper_key: XXXXXXXX
title: TAP-Vid: A Benchmark for Tracking Any Point in a Video
collection: MyPapers
zotero_pdf: <zotero_data_dir>/storage/<item>/xxx.pdf
pdf_sha256: ...
translated: 2026-11-03
translation_model: deepseek-chat
---

# TAP-Vid: A Benchmark for Tracking Any Point in a Video

## Abstract

Generic motion understanding from video involves not only tracking objects... ^p1

视频中的通用运动理解不仅涉及跟踪物体……

## 1 Introduction
...
```

规则：
- 标题层级与 PDF 目录一致（h1 论文名 / h2 章节 / h3 小节）。
- 每段英文结尾带块锚点 `^p<N>`（N 全文递增，稳定可复算）；中文译文**直接作为下一段**（不用 `>` 引用块，避免把后面的表格/代码块吞进 blockquote）。
- **公式统一用 Markdown 数学语法：行内 `$...$`、行间 `$$...$$`**（不翻译公式；不用图片，也不用 `_x_` 下划线模拟上下标）。公式图升级用 `paperread formulas`，中文行内数学片段用 `paperread mathfix` 修正。
- 表格原样保留；图注翻译。
- 图片统一 `![](assets/fig<N>_<slug>.png)`（相对 markdown 链接，移动目录不破链）。
- 参考文献默认不翻译（`## References` 保留原文），可配置。
- 每页插入 `<!-- page N -->` 便于定位。

### 3.2 `note.md`（精读笔记）

沿用现有样例骨架：
`# 简称 — 全称` → 一句话总结（引用块）→ 主图 → Metadata 表 → `## 1. 问题动机` → `## 2. 任务定义` → `## 3. 方法`（分子节）→ `## 4. 训练数据 & 实验设置` → `## 5. 实验` → `## 6. 实现细节速查` → `## 7. 局限`（作者自述 + 个人评注）→ `## 8. 阅读总结`（可迁移思想 / 评测技巧 / 与本课题关系 / 不适用场景 / 阅读线索）。

链接规则：
- 引用原文句：`> 原文："..."（[↓原文](full.md#^p87)）`
- 引用图片：`![](assets/fig1_task_overview.png)` + 斜体图注写「该看什么」
- 笔记间：`[PIPs](../ThirdPaper-ZZZZZZZZ/note.md)`
- frontmatter：`paper_key / title / collection / tags / status / date`

### 3.3 assets 命名

`fig<N>_<英文slug>.png`（图）、`tab<N>_<slug>.png`（表格截图，可选）、`eq<N>_<slug>.png`（公式图）。
不再加论文前缀——assets 已在论文目录内，目录即命名空间。
优先用 PDF 内嵌图（pymupdf4llm 已提取）；矢量图/整页拼图用图注定位裁剪（参考 `tools/pdf_figures.py` 的图注定位思路）。

## 4. 流水线与命令

| 步骤 | 命令 | 输入 → 输出 |
|---|---|---|
| 1 扫描 | `paperread scan` | Zotero（只读）→ `.paperread/library.json` + 摘要 |
| 2 规划 | `paperread plan` | library.json + vault 现状 → `.paperread/plan.json` + 树状预览 |
| 3 抽取 | `paperread extract <key>` | PDF → `_fulltext.src.md` + assets + blocks.json |
| 4 翻译 | `paperread translate <key>` | blocks.json → `<简称>_fulltext.md`（中英交替+锚点） |
| 5 阅读包 | `paperread packet <key>` | fulltext + 图 + 元数据 → `.paperread/packets/<key>.md` |
| 6 笔记 | agent 读 skill 模板撰写 | packet + fulltext → `<简称>_note.md`（+ `<简称>_summary.md`） |
| 7 校验 | `paperread verify <key>\|--all` | 链接/锚点/图片/frontmatter → 报告 |

批量：`paperread run --collection MyPapers`（3→7 全流程，笔记步骤转交 agent 或 API 兜底）。

## 5. 增量与状态

`state.json`：
```json
{"version": 1, "papers": {"XXXXXXXX": {"short": "TAP-Vid", "dir": "MyPapers/SomePaper-XXXXXXXX",
  "pdf_sha256": "...", "pdf_size": 123, "pdf_mtime": 1.7e9, "fulltext_at": "...", "note_at": "..."}}}
```
状态机：`new`（目录不存在）→ `stale`（PDF 哈希变）→ `exists`；目录存在但 key 不在库中 → `orphan`（只报告，不删除）。
Zotero 集合改名/移动：报告「建议迁移」，不自动移动（避免破坏双链）。

## 6. 技术选型

| 环节 | 选型 | 理由 |
|---|---|---|
| Zotero | sqlite 只读副本（WAL 一起拷）→ 本地 HTTP API（可选） | 无需 key、不锁库；本地 API 需在 Zotero 设置勾选「允许其他应用通信」 |
| PDF 解析 | PyMuPDF 2.1 + pymupdf4llm | 实测 TAP-Vid：42 标题 / 52 图 / 表格还原 / 公式成图，数字版 PDF 无需 MinerU |
| 图形裁剪 | pymupdf 渲染 + 图注 bbox（poppler 可选） | 复用样例 `pdf_figures.py` 的「图注定位 + 墨迹范围」思路 |
| 翻译 | DeepSeek API（OpenAI 兼容）+ 批处理/缓存/术语表 | 便宜可控可重跑；一篇约 3 万 token |
| 笔记 | DSH agent + `.agents/skills/paper-read/SKILL.md` | 质量对齐样例，避免一次性 prompt 幻觉 |
| 状态 | vault 内 `.paperread/*.json` | 随库迁移，Obsidian 隐藏 |

## 7. 里程碑与验收

- **P0（本次）**：项目骨架 + `scan` + `plan`；对真实库输出清单与目录规划；demo vault 建立。验收：11 篇在集合内论文 + 5 篇孤儿 PDF 全部列出，状态正确。
- **P1**：单篇端到端（extract → translate → assets → packet → 笔记 → verify）。验收：与样例笔记逐节对照；`verify` 全绿；笔记中至少 20 处引用带可点击锚点。
- **P2**：批量 10 篇 + 增量同步 + `status` 覆盖率报告。
- **P3（可选）**：Zotero 高亮写回/深链（复用朋友工具 `annotations.py`）、MCP server、Dataview 看板、问答/综述。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| Zotero 运行时 DB 被锁 / WAL 未合并 | 拷贝 sqlite+wal+shm 到临时目录再读；失败重试一次；本地 API 作为备选 |
| 双栏 PDF 段落顺序错 | pymupdf4llm 布局解析 + 人工抽检；异常页回退按栏排序 |
| 公式/表格翻译错位 | 公式成图不翻译；表格整块不拆；块级翻译 + 逐块回填 |
| 笔记幻觉（数字/结论编造） | 引用必须带 `^p` 锚点；`verify` 抽查数字是否出现在 fulltext；agent skill 明令「无出处不下结论」 |
| API key 泄露 | 只放 `config/settings.toml`（gitignore），不回显、不入库 |
| 目录改名破坏双链 | 不自动改名；state 记录映射，变更只报告 |

## 9. 待定

- DeepSeek API key 来源（DSH 凭据里已有 `DEEPSEEK_API_KEY`，是否直接复用）。
- 是否需要英文版笔记 / 双语 frontmatter。
- 是否把样例笔记纳入 demo vault 作为「已完成」基准。

## 10. 进展

- **P0 完成（2026-11-03）**：`scan` / `plan --apply` 已对真实库跑通。Zotero 20 个集合、16 篇论文条目（11 篇在集合内且 PDF 可用；5 篇孤儿条目 PDF 文件已丢失；1 条在回收站）。demo vault `<vault>` 已建 11 个论文目录。
- **P1 完成（2026-11-03）**：
  - TAP-Vid 端到端：`extract`（34 页 → 358 块 / 19 图 / 4 公式）→ `translate`（百度，167 块 / 7.3 万字符 / 131 段中英对照）→ `packet` → agent 写 `TAP-Vid_note.md`（400 行 / 166 链接）+ `TAP-Vid_summary.md`（53 行）→ `verify` **166/166 链接、7/7 图片、5/5 引文，0 error**。
  - MyPapers 5 篇 + MyPapers2 5 篇 + MyPapers3 1 篇，共 **11 篇全部完成精读笔记 + 一页速览**，`verify --all` 全绿（链接 1944/1944、图片 88/88、引文 134/134，0 error）。
- **批量能力**：11 篇全部完成抽取 + 英文源 + 写作包；`status` 用 `F/f/N/S` 标记覆盖度（F=中英全文，f=仅英文）。
- **翻译现状**：TAP-Vid 全篇中英对照（百度）；Orca 前 29 块为样例；其余 9 篇为英文源（`translate --dry-run`）。批量翻译待 provider 决策。
- **数据源**：已切换为 Zotero 本地 API 优先（用户已开启），自动排除回收站/已删集合；不可用时回退 sqlite。实测库：4 个存活集合、11 篇可用论文。
- **孤儿处理**：`audit` 分类 + `[ignore]` 配置（Zdraft 集合 + 5 个已删 PDF 的孤儿 key 已忽略）。
- **矢量图/无图注回退**：pymupdf4llm 抓不到图时按图注位置裁页；连图注都没有的论文（PIPs）按页面大图区域裁切并插到页首。DeepSeek-V2 补出 9 张图、PIPs 补出 4 张（架构图已验证完整）。
- **翻译**：DeepSeek / 百度双 provider（`--provider` 可覆盖）；百度已实测可用，质量与额度见交付说明；翻译前过滤封面/作者/参考文献/公式碎片，省 30–40% 额度。
- **解析后端**：PyMuPDF（默认，本地、快）与 **MinerU 云端 API**（可选，`extract --backend mineru`）双后端。MinerU 实测：TAP-Vid 解析 32 秒 → 11.6 万字符 markdown / 220 处 LaTeX 公式 / 6 张 HTML 表格 / 15 张图，公式与表格质量明显优于 PyMuPDF；但 MinerU **只有解析 API，没有翻译 API**（官方文档、API schema、FAQ 均无 translate），翻译仍需百度/其他服务。
- **翻译**：三条路线已打通——① DeepSeek API（按要求暂停）；② 百度 API（实测可用，TAP-Vid 全篇完成）；③ **agent 翻译工作流**（`--emit-tasks` 导出块 → agent 逐块翻译 → `--import-json` 导入渲染，不调用外部翻译 API）。已完成 agent 翻译：PIPs / CoTracker3 / TAPIR / TAPNext++ / DeepSeek-V2；进行中：DeepSeek-V4 / DeepSeekMoE / ST-MoE / PANODIFFUSION。
- **工程**：`pytest` 14 个单测（命名/规划忽略/verify 锚点/翻译块筛选/agent 翻译工作流/图注正则），全绿。
- **P2 完成（2026-11-03）**：11 篇论文全部产出 `assets/` + **中英对照全文**（1389 段对照 / 2746 个 `^pN` 锚点）+ 精读笔记（11 篇，共 4137 行）+ 一页速览（11 篇，557 行）；`verify --all` 全绿（链接 1944/1944、图片 88/88、引文 134/134，0 error）。
  - MyPapers 5 篇、MyPapers2 5 篇、MyPapers3 1 篇；集合结构 1:1 镜像 Zotero。
  - 翻译：TAP-Vid 用百度 API；其余 10 篇用 agent 翻译工作流（不调用外部翻译 API）。
- **公式升级**：`paperread formulas` 把 `assets/eq*.png` 原地替换为 LaTeX（导出任务 → agent 视觉识别 → 导入重渲染），保留块 id 与 `^pN` 锚点，笔记链接不失效。候选同时包含 `eq*.png` 与**短宽条图**（抽取器有时把公式裁成 `fig*.png`）；同一图内多行公式组用 `\begin{aligned}` 保留为一个 `$$` 块。**已完成全库升级**：11 篇共 108 个行间公式转为 `$$...$$`；剩余未转的都是真图/封面（PANODIFFUSION Fig.13/14 图块、DeepSeek-V4 架构图/曲线等，已逐张确认非公式）。
- **行内公式**：`paperread mathfix` 把中文译文里的数学片段改写为 `$...$`，全库共修正 330 段；协议已写入模板/技能/文档：**行内 `$...$`、行间 `$$...$$`**，禁止用图片或 `_x_` 模拟上下标。
- **抽取损坏修复**：PDF 抽取会把行内公式打散成 `<sup>`/下划线碎片；新增 `paperread page`（PDF 页渲染 PNG）+ `paperread fixsrc`（按页图重建 src 与 zh，并自动重映射译文缓存、同步 src.md），10 篇共修复 **314 个段落**。表格同理：`paperread tables` 用 MinerU HTML 表格转 **Markdown 表格**（Obsidian 才能渲染表格内 `$...$`），全库 93 张表升级。
- **表格**：pymupdf4llm 对合并表头/多行表头会产出破损的管道表格；用 `paperread tables` 调 MinerU 解析，把 HTML 表格（保留 rowspan/colspan）原地替换进 blocks（锚点不变），全库 57+ 张表已升级。
- **页码噪声**：PDF 页脚页码会被抽成独立段落，抽取阶段过滤纯数字段落（已清理存量 247 个）。
- **P3（可选，未做）**：Zotero 高亮写回/深链、MCP server、Dataview 看板、问答/综述。
- **Skills**：`.agents/skills/paper-read/SKILL.md` 已创建并被 DSH 目录发现。

