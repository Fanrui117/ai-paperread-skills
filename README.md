# PaperRead

> 仓库名：**ai-paperread-skills**（Zotero → Obsidian 论文阅读流水线 + DSH skill）

**Zotero → Obsidian 论文阅读流水线**：只读扫描 Zotero 库 → 按集合结构镜像目录 → 抽取 PDF（图 / 公式 / 表格）→ 生成中英对照全文（段级交替 + `^pN` 锚点）→ agent 写精读笔记（引用可回链原文）→ 自动校验。

- 形态：Python CLI + DSH skill（也可被其他 agent/脚本调用）
- Zotero **默认只读**：扫描/查找不改库；可选 `ingest` 通过 Zotero Connector 写入新论文
- 输出是标准 Obsidian vault，可直接用 Obsidian 打开

```
<vault>/<集合>/<简称>-<ZoteroKey>/
├── note.md       精读笔记（引用带 [↓原文](translated.md#^pN) 回链）
├── summary.md    一页速览
├── translated.md 中英对照全文（每段 ^pN 锚点）
├── src.md        英文源
└── assets/       图片（fig*.png / eq*.png / img*.jpg）
```

---

## 0. 架构与数据流

三个主体：**Zotero（论文源）**、**paperread CLI（流水线）**、**Obsidian（阅读端）**，agent 负责需要判断力的环节（翻译、写笔记、公式修复）。

![架构图](docs/architecture.svg)

（`docs/architecture.svg` 可直接用浏览器打开；下同）

```
                    ┌──────────────────────────────┐
                    │          DSH Agent           │
                    │  翻译 / 写笔记 / 看页图修公式   │
                    └───────▲───────────┬──────────┘
             emit-tasks     │           │  import-json
                    ┌───────┴───────────▼──────────┐
  ┌──────────┐      │        paperread CLI         │      ┌──────────────┐
  │  Zotero  │─────▶│ scan/find  →  plan/extract   │─────▶│   Obsidian   │
  │ 论文库    │ 只读  │ translate  →  packet/verify  │ 写入  │  Vault       │
  └────▲─────┘      └──────────────────────────────┘      └──────────────┘
       │ ingest（可选写库）
  ┌────┴────────────┐
  │ Zotero Connector │  /connector/saveItems + saveAttachment
  └─────────────────┘
```

| 主体 | 职责 | 读写 |
|---|---|---|
| Zotero | 论文元数据 + PDF 附件 | 扫描只读；`ingest` 时通过 Connector 写入 |
| paperread CLI | 扫描、规划、抽取、翻译编排、校验 | 读 Zotero；写 `<vault>/.paperread/` 与论文目录 |
| DSH Agent | 翻译、精读笔记、公式/表格修复、页图核对 | 读任务 JSON；写结果 JSON；不直接改库 |
| Obsidian | 阅读 note.md / translated.md / 图表 | 只读展示 |

---

## 1. 环境要求

| 依赖 | 说明 |
|---|---|
| Python | 3.11+（推荐 3.12） |
| [uv](https://docs.astral.sh/uv/) | 包管理（也可用 pip） |
| [Zotero](https://www.zotero.org/) 7/8 | 论文库；PDF 附件需在 Zotero 里 |
| [Obsidian](https://obsidian.md/) | 打开生成的 vault（可选，但推荐） |
| DSH（可选） | 用 agent 写笔记 / 翻译 / 修复公式时需要 |

支持 Windows / macOS / Linux；命令示例以 Windows 为主，其他系统把 `.venv\Scripts\paperread.exe` 换成 `.venv/bin/paperread`。

---

## 2. 安装

```bash
git clone <this-repo> ai-paperread-skills
cd ai-paperread-skills

# 方式 A：uv（推荐）
uv sync

# 方式 B：pip
python -m venv .venv
.venv/bin/pip install -e .        # Windows: .venv\Scripts\pip install -e .
```

安装后验证：

```bash
.venv/bin/paperread --version
.venv/bin/paperread --help
```

---

## 3. 初始化与配置

```bash
.venv/bin/paperread init      # 生成 config/settings.toml + vault 目录
.venv/bin/paperread doctor    # 自检：配置 / Zotero / vault / key / 依赖
```

`config/settings.toml` 已被 `.gitignore` 忽略（不会提交密钥）。所有配置项见 `config/settings.example.toml`，常用项：

### 3.1 vault 与 Zotero

```toml
[vault]
root = "~/PaperNotes"        # Obsidian vault 根目录（会自动创建）
library_subdir = ""          # 论文库放 vault 子目录时填写，如 "Papers"

[zotero]
data_dir = ""                # 留空自动探测 ~/Zotero；也可写绝对路径
local_api = "http://127.0.0.1:23119/api"
prefer_local_api = true      # 优先本地 API；未开启时自动回退 sqlite 只读副本
```

Zotero 侧准备（只需一次）：

1. Zotero → **设置 → 高级 → 勾选「允许其他应用与 Zotero 通信」**（本地 API 端口 23119）；
2. 确认要处理的论文 PDF 已作为附件下载到本地（`storage/` 下有文件）。

> 工具始终只读：拷贝 `zotero.sqlite`（含 wal/shm）到临时目录查询，不锁库、不改库。

### 3.2 翻译：三条路线，任选其一

| provider | 需要什么 | 适用 |
|---|---|---|
| `agent`（默认，推荐） | 不需要任何 key，由 DSH/agent 逐块翻译 | 质量最好、零外部费用 |
| `baidu` | 百度翻译 appid + secret | 想全自动批量、可接受机翻质量 |
| `deepseek` | DeepSeek API key | 想全自动且质量较好 |

> `agent` 路线**不调用任何外部翻译 API**（不消耗 DeepSeek/百度余额）；`baidu` / `deepseek` 会在执行前打印「将产生费用」的提示，可用 `--dry-run` 只渲染英文。

```toml
[translate]
provider = "agent"          # agent | baidu | deepseek

# --- deepseek ---
api_base = "https://api.deepseek.com"
model = "deepseek-chat"
api_key = ""                # 也可以放环境变量 DEEPSEEK_API_KEY
api_key_env = "DEEPSEEK_API_KEY"
use_dsh_credentials = false # true 时从 ~/.dsh/.credentials.yaml 的 refs 读取

# --- baidu ---
baidu_appid = ""
baidu_secret = ""
```

**key 的获取**：

- 百度翻译：<https://fanyi-api.baidu.com/> → 管理控制台 → 开发者信息（标准版每月 5 万字符免费，超出按量计费）
- DeepSeek：<https://platform.deepseek.com/> → API keys
- key 的三种来源优先级：`settings.toml` 里的 `api_key` → 环境变量 → `~/.dsh/.credentials.yaml`

### 3.3 可选：MinerU 云端解析（公式 / 表格质量更好）

```toml
[mineru]
api_key = ""                 # https://mineru.net/ → API 管理页创建 Token
api_base = "https://mineru.net/api/v4"

[extract]
backend = "pymupdf"          # pymupdf（默认，本地免费）| mineru（云端，需 key）
```

> 注意：**切换解析后端会改变段落切分 → `^pN` 锚点全变 → 已有笔记链接失效**。建议只对未处理的论文用 MinerU，或接受重新生成笔记。

### 3.4 其他

```toml
[note]
language = "zh"
write_tags = true

[ignore]                     # 不进 vault、不报错的条目
collections = ["草稿箱"]      # 集合名子串匹配
items = []                   # Zotero item key（8 位）

[names]                      # 人工覆写论文简称
"Particle Video Revisited: Tracking Through Occlusions Using Point Trajectories" = "PIPs"
```

---

## 4. 快速开始

两条入口：**全库**（一次性建好所有目录）和**单篇**（只处理当前想读的那篇，不扫全库）。

### 4.1 全库流程

```bash
paperread scan                 # 只读扫描 Zotero → <vault>/.paperread/library.json
paperread plan                 # 预览目录规划
paperread plan --apply         # 创建所有论文目录（写 state）
paperread status               # 查看覆盖率（F=全文 N=笔记 S=速览）
```

### 4.2 单篇流程（推荐日常使用）

新论文加进 Zotero 后，不用全库扫描，直接按标题/DOI/key 定位：

```bash
paperread find "point tracking"     # 只读查找：列出 key / 标题 / 集合 / PDF 状态
paperread add <key> --run           # 建这一篇的目录并立刻 extract → translate → packet
# 或分步：
paperread add <key>                 # 只建目录
paperread extract <key>             # PDF → src.md + assets/ + blocks.json
paperread formulas <key> --emit-tasks   # 公式图 → LaTeX（agent 识别后 --import-json；或 extract --backend mineru）
paperread translate <key> --emit-tasks   # 交给 agent 翻译，再 --import-json 导入
paperread packet <key>              # 写作包 → agent 写 note.md / summary.md
paperread notesync <key>            # 笔记里的公式图 → LaTeX
paperread verify <key>              # 校验链接/锚点/图片/引文
```

`add` 只处理这一篇，不会创建其它论文的目录；多篇同时新增时也只影响你指定的那篇。

### 4.3 搜索并入库（按主题找论文）

不确定具体是哪几篇时，先在线检索，再挑选入库：

```bash
paperread search "point tracking 2024" --limit 10
# 输出候选列表（arXiv / Crossref 去重），并保存到 <vault>/.paperread/searches/*.json

paperread collect <上面保存的 json> --indices 1,3-5 --collection "MyPapers" --run
# 把选中的候选写入 Zotero（指定集合），并立刻 extract → translate → packet
```

- `search` 只读、不写库；结果里的 arXiv ID / DOI 可直接再用于 `ingest`
- `collect` 支持 `1,3-5` 区间；`--run` 时入库后自动建目录并跑流水线，之后让 agent 写笔记即可
- 检索源可选：`--source arxiv | crossref | all`

### 4.4 入库到 Zotero（单篇）

已有论文可以直接从 Zotero 浏览器插件保存；如果想让 agent 代劳：

```bash
paperread ingest 2401.12345                 # arXiv ID
paperread ingest 10.1000/xyz --collection "MyPapers"   # DOI + 指定集合
paperread ingest https://arxiv.org/abs/2401.12345 --run # 入库并立刻处理进 vault
```

实现方式：调用 Zotero 桌面版自带的 **Connector 接口**（浏览器插件用的同一套协议）：
`/connector/saveItems` 保存元数据 → `/connector/updateSession` 移动到指定集合 → `/connector/saveAttachment` 上传 PDF。
不直接改 `zotero.sqlite`，因此不会破坏库；限制是只能写入个人库、不能更新/删除已有条目。

> 需要 Zotero 正在运行，并勾选「允许其他应用与 Zotero 通信」。没有装 Zotero 或不想写库时，用浏览器插件入库后跑 `paperread find` + `paperread add` 即可。

### 4.5 用 DSH agent 跑完整流程

本仓库自带 skill：`.agents/skills/paper-read/SKILL.md`。在 DSH 中打开本项目（或其父目录含 `.agents/skills`）即可被识别，然后直接说：

> 帮我把 Zotero 里 xxx 这篇论文做成 Obsidian 笔记

也可以按主题批量找：

> 帮我找 2024 年 point tracking 方向的论文，选前 5 篇入库到 MyPapers，并整理成笔记

skill 会走「search → 候选确认 → collect --run → packet → 写笔记 → verify」。

skill 会按「find/add → extract → translate(agent) → packet → 写笔记 → verify」执行；需要新论文入库时说「把 arXiv xxx 入库并整理」。

**把 skill 注册到自己的项目**：

```bash
# 复制到你的项目根目录（DSH 从 <projectRoot>/.agents/skills/ 发现技能）
mkdir -p <你的项目>/.agents/skills
cp -r .agents/skills/paper-read <你的项目>/.agents/skills/
```

skill 里写的是相对命令与约定，换机器后只需改 `SKILL.md` 顶部的项目根路径。

---

## 5. 命令参考

每个子命令都有**中文别名**，中英文等价（如 `paperread 扫描` = `paperread scan`）。

| 中文别名 | 英文命令 | 说明 |
|---|---|---|
| `初始化` | `init` | 生成 `config/settings.toml` 与 vault 目录 |
| `自检` | `doctor` | 环境自检（配置 / Zotero / vault / key / 依赖） |
| `扫描` | `scan` | 只读扫描 Zotero → `vault/.paperread/library.json` |
| `审计` | `audit` | 审计：可用 / 缺 PDF / 孤儿 / 已忽略 |
| `规划` | `plan [--apply]` | 对照 vault 输出目录规划；`--apply` 建目录并写 state |
| `查找` | `find <query>` | 只读查找：key / 标题片段 / DOI / 作者 → 列出 key、集合、PDF 状态 |
| `搜索` | `search <query> [--limit] [--source]` | 在线检索 arXiv/Crossref 候选论文（不写库，结果存 JSON） |
| `批量入库` | `collect <search.json> [--indices] [--collection] [--run]` | 把候选批量写入 Zotero，可选接着跑流水线 |
| `添加` | `add <query> [--run]` | 只处理单篇：按 key/标题/DOI 建目录，`--run` 直接跑流水线 |
| `入库` | `ingest <arxiv\|doi\|url> [--collection] [--run]` | 通过 Zotero Connector 写入新论文（可选集合），再处理进 vault |
| `抽取` | `extract <key> [--force] [--backend mineru]` | PDF → `src.md` + `assets/` + `blocks.json` |
| `公式` | `formulas <key> --emit-tasks \| --import-json <f>` | 公式图 → `$$...$$` LaTeX（保留锚点）；`run/add/ingest --formulas` 可自动导出任务 |
| `翻译` | `translate <key> [--dry-run] [--provider ...] [--main-only]` | 生成中英对照 `translated.md` |
| `翻译` | `translate <key> --emit-tasks` / `--import-json <f>` | agent 翻译工作流（导出块 → 逐块译 → 导入） |
| `写作包` | `packet <key>` | 笔记写作包（大纲 / 锚点 / 图清单 / 摘要） |
| `行内公式` | `mathfix <key> --emit-tasks \| --import-json <f>` | 中文译文里的行内数学 → `$...$` |
| `表格` | `tables <key> [--emit-tasks] [--import-json <f>]` | MinerU HTML 表格 → Markdown 表格（表格内公式可渲染）；`--emit-tasks` 导出疑似破损表头 + 页图给 agent 重写 |
| `修源` | `fixsrc <key> --import-json <f>` | 修复被 PDF 抽取破坏的源段落（`{id: {src, zh}}`，缓存自动重映射） |
| `笔记同步` | `notesync <key> \| --all [--dry-run]` | 把笔记里已升级为 LaTeX 的公式图替换成 `$$...$$`（其它图保留） |
| `图片排版` | `imggrid <key> --emit-tasks \| --import-json <f>` | 同图多裁片排版：导出图片组+页图，agent 决定并排/竖排/自定义后导入 |
| `页面` | `page <key> <n>` | 渲染 PDF 第 n 页为 PNG（agent 核对公式/表格用） |
| `校验` | `verify <key> \| --all` | 校验链接 / 锚点 / 图片 / frontmatter / 引文 |
| `一键运行` | `run <key> \| --collection X` | extract → translate → packet 一条龙 |
| `索引` | `index` | 生成 vault 根目录 `_索引.md` |
| `状态` | `status` | 覆盖率（F=全文 N=笔记 S=速览） |

单篇处理（日常最常用）：

```bash
paperread 查找 "point tracking"       # 只读，看看库里有哪几篇
paperread 添加 <key> --run            # 只建这一篇目录并跑 extract→translate→packet
paperread 入库 2401.12345 --collection MyPapers --run   # 新论文：写入 Zotero 并处理
```

中文别名的典型用法：

```bash
paperread 自检                       # = paperread doctor
paperread 扫描 && paperread 规划 --apply
paperread 抽取 <key> && paperread 翻译 <key> --provider baidu
paperread 校验 --all
```

---

## 6. 目录与格式约定

### 6.1 文件命名

论文目录 `<AI简称>-<ZoteroKey>/` 下固定四个短文件：

| 文件 | 内容 |
|---|---|
| `note.md` | 精读笔记（结构见 `templates/note-template.md`） |
| `summary.md` | 一页速览 |
| `translated.md` | 中英对照全文（每段 `^pN` 锚点；旧名 `full.md` 仍兼容） |
| `src.md` | 英文源（翻译输入，`fixsrc` 会同步） |

### 6.2 链接与数学（重要）

- 笔记里一律用**相对 Markdown 链接**：
  - 原文回链：`[↓原文](translated.md#^p7)`
  - 图片：`![](assets/fig1_xxx.png)`（Obsidian 子目录里 `![[assets/x]]` 是 vault 根相对，会找不到）
  - 跨论文：`[PaperB](../PaperB-YYYYYYYY/note.md)`
- 图片排版：同一张图的多张裁片，**尺寸相近时自动并排**（每行 3 张，Markdown 表格）；组合图、标签条（如 (a)(b)(c) 条）不并排，避免压扁。复杂组合图用 `paperread imggrid <key>` 导出页图与图片组，让 agent 看原图后决定 `grid` / `stack` / `raw`（自定义 HTML 或 Markdown）。
- 公式：**行内 `$...$`、行间 `$$...$$`**；不要用图片或 `_x_` 模拟上下标。
- `verify` 会检查：链接目标存在、锚点存在、图片存在、frontmatter `paper_key` 一致、引文能在全文找到。

### 6.3 增量与状态

`<vault>/.paperread/` 下：

- `library.json`：scan 结果（论文、集合、PDF 路径）
- `plan.json`：最近一次规划
- `state.json`：key → 目录 / PDF 哈希 / 产物时间 / 解析后端
- `work/<key>/`：`blocks.json`、翻译缓存、任务文件（agent 工作流用）

PDF 更新后 `plan` 会标 `stale`；`extract --force` 重新抽取。

---

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| `Local API is not enabled` | 正常：自动回退 sqlite 只读副本；想用 API 见 §3.1 |
| 笔记里图片显示「找不到」 | 检查是否用了 `![](assets/...)` 相对路径；`![[assets/...]]` 会失败 |
| 公式显示为原始 `$...$` | Obsidian 设置里启用 MathJax；表格内公式需 Markdown 表格（本工具已默认输出） |
| 换 MinerU 后端后笔记链接全断 | 锚点随解析变化；重新 `translate` 并重新生成笔记，或只对新论文用 MinerU |
| 翻译 401 / 403 | 检查 `provider` 与对应 key；`paperread doctor` 会列出缺失项 |
| Zotero 库被锁 | 工具先拷贝副本再读；仍失败时关闭 Zotero 重试 |
| `verify` 报锚点不存在 | 全文被重新抽取过（锚点变化）；重新生成笔记或改回旧 blocks |

---

## 8. 设计要点

- **Zotero 默认只读**：本地 API 优先，回退 sqlite 只读副本；写库只走可选的 `ingest`（Zotero Connector，浏览器插件同协议）。
- **全库 / 单篇分离**：`scan`/`plan` 管全库；`find`/`add` 只处理单篇，不创建其它目录。
- **锚点可追溯**：全文每段 `^pN`；笔记引用带 `#^pN` 回链；`verify` 全量校验。
- **agent 工作流**：翻译 / 公式识别 / 表格修复 / 源段落修复都采用「导出任务 JSON → agent 处理 → 导入」模式，不需要额外 API 也能产出高质量结果。
- **可替换后端**：解析（PyMuPDF / MinerU）、翻译（agent / 百度 / DeepSeek）都可切换。
- **失败可见**：抽取/翻译失败直接报错，不产出半成品。

---

## 9. 开发与测试

```bash
uv sync                      # 含 dev 依赖（pytest）
.venv/bin/pytest -q          # 26 个单测：命名/规划/verify/翻译/公式/表格/fixsrc/查找
.venv/bin/paperread --help
```

代码结构：

```
src/paperread/
├── cli.py         命令行入口
├── config.py      配置加载（toml + 环境变量）
├── zotero.py      sqlite 只读副本
├── zotero_api.py  Zotero 本地 API
├── zotero_connector.py  Zotero Connector 写入（ingest 用，可选）
├── ingest.py      单篇查找 / 元数据抓取（arXiv/DOI）/ 入库
├── naming.py      目录/文件命名
├── plan.py        规划与增量状态（支持单篇过滤）
├── extract.py     PDF 抽取（PyMuPDF / MinerU）
├── mineru.py      MinerU 云端解析
├── translate.py   翻译与全文渲染
├── mathfix.py     行内公式修正 / 源段落修复
├── formulas.py    公式图 → LaTeX
├── tables.py      表格升级（MinerU → Markdown）
├── tablemd.py     HTML 表格 → Markdown 表格
├── packet.py      笔记写作包
├── verify.py      校验
└── index.py       索引页（cli 内调用）
```

- `.agents/skills/paper-read/SKILL.md`：DSH skill
- `templates/note-template.md`：笔记结构模板
- `examples/sample-notes/`：样例笔记（PIPs / TAP-Vid / TAPIR），可作为笔记风格参考
- `config/glossary.tsv`：翻译术语表（点跟踪 + AI infra 两域，可自行增删）
- `docs/plan.md`：方案与决策记录

---

## 10. 已知限制

- PDF 表格 / 公式抽取依赖版式：合并表头需 `tables`（MinerU）升级；个别论文公式需 `fixsrc` 对照页图人工/agent 修复。
- agent 工作流需要 agent 执行（DSH 或其他支持文件读写的 agent），纯命令行环境请用百度/DeepSeek。
- 笔记为草稿（`status: draft`），建议人工校对后转正式。

## License

MIT
