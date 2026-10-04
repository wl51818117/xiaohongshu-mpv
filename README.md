# xiaohongshu-mpv

> 小红书电商内容运营自动化工作台 · MVP
> 单账号版 | Agent 编排驱动 | 前后端分离

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-087ea4?logo=react&logoColor=white)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-6-646cff?logo=vite&logoColor=white)](https://vite.dev/)

---

## 一、项目简介

把小红书内容生产的完整链路搬进程序，由 **dsh harness（Agent 运行时）** 编排驱动，人只做关键确认。

```
RSS 采集 → 素材库 → AI 选题转换 → 选题库 → 稿件（确定性校验）
  → 素材工坊（生图 / 图生视频 / ffmpeg 合成）
  → 发布包 + 12 项自检 → 半自动发布（人工点确认）
  → 数据回流 → 爆文要素提炼 → 回流选题库（闭环）
```

### 设计底线

| 铁律 | 原因 |
|---|---|
| **不碰小红书平台数据** | 采集只走公开 RSS。曾有爬取数据被判**有期徒刑三年**、判赔 **110 万**的判例 |
| **不做 AI 全自动发布** | 平台 2026 明确封禁 AI 托管账号（已处置 80 万+）；风控含鼠标行为指纹 |
| **AI 内容必须主动标识** | 不标识即违规；平台明确「主动标注**不影响流量**」 |
| **校验用确定性规则** | 平台红线不能靠 LLM 猜 |

---

## 二、功能特性

### 已实现（48 个接口）

| 模块 | 能力 |
|---|---|
| **采集** | RSS 多赛道预设、内容指纹去重、单源失败隔离、**源可用性探测** |
| **选题** | 四条转换规则（换视角/落人群/给增量/合规过筛）、合规黑名单前置、因子评分 |
| **稿件** | **必须从选题派生**、确定性规格校验（标题/埋词/标签/AI声明）、AI 按写作简报生成正文 |
| **素材工坊** | 封面 3 候选（3:4）、内页 4-8 张、图生视频运镜方案、ffmpeg 合成、素材规格校验 |
| **发布包** | 组装 + **12 项发布前自检**，任一不通过仍返回完整报告 |
| **数据看板** | 数据快照、投流建议（阈值来自平台调研）、爆文要素提炼、评论挖选题、回流选题 |
| **Agent 编排** | 内核 7 个业务工具 + **6 个元能力**（让 Agent 动态改工作台） |
| **配置** | 多套 API 配置、**Fernet 加密存储**、连接测试六态、模型清单拉取与手动兜底 |
| **RSS 管理** | 可视化增删改源、按赛道分组、一键批量体检 |

### 亮点：Agent 能自己改工作台

不只在编译期固化能力。**通过 hbridge 的 `bridge.register()` 运行时注册**：

- `ui_add_panel` 动态建面板（指标卡/列表/详情/文本四型）
- `wb_read_data` / `wb_write_data` 读写任意接口
- `ui_remove_panel` / `ui_list_panels` 管理面板

> 之后想让工作台多什么功能，**对 Agent 说一句即可，不用改代码重新构建**。

---

## 三、环境依赖

### 必需

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 后端运行时 |
| Node.js | 22+ | 前端构建 |
| ffmpeg | 任意近期版本 | 视频合成（可选，仅视频链路需要） |
| **dsh 内核** | hbridge v2.1+ | Agent 运行时，独立项目 |

### 数据库

- 开发：**SQLite**（零依赖，开箱即跑）
- 生产：**PostgreSQL**（改 `DATABASE_URL` 即可）

---

## 四、安装与运行

### 1. 准备内核（Agent 运行时）

```bash
npm i -g @deepseek-ai/dsh
# 配置 bridge 凭据（票据密钥与appToken）
cd <harness目录> && node scripts/setup-bridge.mjs
# 启动（内核 8787 + 语音网关 8788）
node start-all.js
```

> ⚠️ **必须用 `start-all.js` 启动** —— 它会自动修复 profile 的 skill provider 冲突。直接跑 `dsh --profile kernel` 会拒启动。

### 2. 启动后端

```bash
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Windows 也可直接双击 `backend/start_mvp.bat`。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 4. 一键启动

```bash
# 项目根目录（Windows）
start-all.bat
```

启动后访问：

| 地址 | 说明 |
|---|---|
<http://127.0.0.1:5173> | **应用界面** |
| <http://127.0.0.1:8000/docs> | 接口文档（Swagger） |

---

## 五、配置说明

### 后端环境变量（`backend/.env`，参考 `.env.example`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/workbench.db` | 换 `postgresql+psycopg://...` 即切 PG |
| `KERNEL_BASE_URL` | `http://127.0.0.1:8787` | 内核地址 |
| `KERNEL_TIMEOUT_SECONDS` | `120` | 内核调用超时 |

### 前端环境

无需配置，Vite 代理 `/api` → `8000`、`/files` → `8000`。

### 应用内配置（推荐，无需改文件）

**设置 → API 配置**

| 字段 | 说明 |
|---|---|
| 配置名称 | 自己区分，如「DeepSeek官方」 |
| Base URL | 如 `https://api.deepseek.com`（须 OpenAI 兼容） |
| API 密钥 | **Fernet 加密落盘**，列表只显示掩码 |
| 模型 | 可从 `/models` 拉取后选择，或手动输入 |

> **连接测试** 六态区分：`success` / `auth_failed`（密钥无效）/ `unreachable`（地址不可达）/ `timeout` / `bad_request` / `error`，每种带耗时与处置建议。
> **未通过测试的配置不可设为生效** —— 避免 Agent 用坏 Key 反复重试。

**设置 → RSS 源**

5 个赛道预设（科技/综合/生活/服饰内衣/电商零售），可视化增删改。

> 💡 RSS 源会失效，建议定期点「全部检测」。探测能区分「连不上」与「能访问但不是 RSS」（后者返回 200 却抓不到条目，很常见）。

---

## 六、目录结构

```
.
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── main.py             # 入口（挂载全部路由）
│   │   ├── core/config.py      # 集中配置
│   │   ├── db/                 # 数据模型与会话
│   │   │   ├── models.py       # 素材/选题/稿件/审计 四表
│   │   │   └── session.py
│   │   ├── services/           # 业务逻辑（纯函数为主，易测）
│   │   │   ├── rss_collector.py
│   │   │   ├── topic_converter.py
│   │   │   ├── topic_to_draft.py    # 选题→稿件
│   │   │   ├── draft_validator.py   # 确定性规格校验
│   │   │   ├── asset_generator.py   # 生图/图生视频/ffmpeg
│   │   │   ├── publish_package.py   # 发布包 + 12项自检
│   │   │   └── analytics.py         # 数据回流与复盘
│   │   ├── agent/kernel.py    # 内核客户端
│   │   └── api/               # 12 个路由模块
│   ├── data/feeds.json        # RSS 源配置（入库）
│   └── requirements.txt
├── frontend/                   # Vite + React 19
│   └── src/
│       ├── lib/                # API 客户端、流程定义
│       ├── bridge/             # hbridge 接入 + 元能力
│       ├── components/         # UI 组件
│       ├── hooks/
│       └── App.tsx
└── docs/                       # 17 份设计与调研文档
```

---

## 七、常见问题

<details>
<summary><b>Q: 改了内核工具但没生效？</b></summary>

内核加载的是 `.dsh/profiles/kernel/tools/index.js`，**不是** `kernel/tools/index.js`（后者只是模板源）。

```bash
cp kernel/tools/index.js .dsh/profiles/kernel/tools/index.js
# 重启内核
```
</details>

<details>
<summary><b>Q: 内核启动失败（skill provider 冲突）？</b></summary>

```
Error: a skill provider named "filesystem" is already registered
```

**必须用 `node start-all.js` 启动**，它会生成临时 patch 自动修复。
</details>

<details>
<summary><b>Q: Agent 报 401？</b></summary>

内核自 hbridge v2.1 起要求鉴权。检查：

1. 设置里API 配置是否「已测试」
2. 后端进程环境变量是否带 `DEEPSEEK_API_KEY`
</details>

<details>
<summary><b>Q: 接口返回 422？</b></summary>

多为请求方式不匹配。如 `/api/assets/generate` **同时支持** JSON body 与 query 参数，两者皆可。
</details>

<details>
<summary><b>Q: 页面空白 / 流程图不显示？</b></summary>

- X6 3.x **不要传`shape:'rect'`**（会抛异常导致白屏）
- React 19 StrictMode 与 X6 冲突，**不要包 StrictMode**
</details>

<details>
<summary><b>Q: 换了机器后 API Key 读不出来？</b></summary>

密钥由**机器信息派生**。换机器后密文解不开（返回空而非崩溃），需重新填写。
</details>

<details>
<summary><b>Q: RSS 抓到 0 条？</b></summary>

页面能访问 ≠ 是 RSS。探测会明确区分「连不上」与「能访问但抓不到条目」。后者需换源或自建 RSSHub。
</details>

<details>
<summary><b>Q: 生图是占位图？</b></summary>

当前 `_call_provider` 是本地占位实现（**尺寸真实、流程完整**）。接真实 API 只需改这一个函数。
</details>

---

## 八、使用示例

### 主流程：选题 → 稿件 → 发布包

```bash
# 1. 采集（选赛道预设）
curl -X POST http://127.0.0.1:8000/api/pipeline/collect \
  -H "content-type: application/json" \
  -d '{"preset":"fashion","limit_per_feed":3}'

# 2. 素材转选题（合规黑名单会拦掉违规题材）
curl -X POST "http://127.0.0.1:8000/api/pipeline/convert?limit=20"

# 3. 从选题转稿件（唯一正确的建稿入口）
curl -X POST http://127.0.0.1:8000/api/drafts/from-topic \
  -H "content-type: application/json" -d '{"topic_id":12}'

# 4. AI 生成正文（带选题写作简报）
curl -X POST http://127.0.0.1:8000/api/drafts/generate-copy \
  -H "content-type: application/json" -d '{"draft_id":2}'

# 5. 生成素材
curl -X POST "http://127.0.0.1:8000/api/assets/generate?draft_id=2&kind=cover&count=3"
curl -X POST "http://127.0.0.1:8000/api/assets/generate?draft_id=2&kind=inner&count=6"

# 6. 发布前 12 项自检
curl http://127.0.0.1:8000/api/publish/package/2
```

### Agent 对话（界面右侧栏）

```
「查一下选题库有什么」          → Agent 调 list_topics
「把第 3 条选题写成稿」        → 转稿件 → AI 生成正文
「加一个选题速览面板」          → Agent 调 ui_add_panel（无需改代码）
```

### 截图占位

<!-- 建议后续补充：主界面 / 流程图 / 素材工坊 / 数据看板 截图 -->

| 模块 | 截图 |
|---|---|
| 主界面（紫色渐变侧栏） | _待补充_ |
| 双链路流程图 | _待补充_ |
| 选题写作台 | _待补充_ |
| 素材工坊 | _待补充_ |
| API 配置面板 | _待补充_ |

---

## 九、文档索引

| 文档 | 内容 |
|---|---|
| [00-平台流程调研结论](./docs/00-平台流程调研结论.md) | 运营全流程、合规红线、投流阈值 |
| [01-harness内核机制与集成方案](./docs/01-harness内核机制与集成方案.md) | 内核机制实测梳理与踩坑 |
| [02-内容规格与合规基线](./docs/02-内容规格与合规基线.md) | 平台硬规格（校验器依据）|
| [03-系统架构设计](./docs/03-系统架构设计.md) | 模块划分与状态机 |
| [04-分阶段落地实施计划](./docs/04-分阶段落地实施计划.md) | P0-P5 实施路线 |
| [08-终极选型方案v4单账号版](./docs/08-终极选型方案v4单账号版.md) | 技术选型决策 |
| [12-变更记录](./docs/12-变更记录.md) | 版本变更与回滚 |
| [13-后续开发待办清单](./docs/13-后续开发待办清单.md) | **未完成功能清单** |
| [14-开发复盘总结](./docs/14-开发复盘总结.md) | 经验沉淀与避坑清单 |
| [15-模块功能缺口分析](./docs/15-模块功能缺口分析.md) | 各模块缺口与优先级 |

---

## 十、当前状态与待办

**已完成**：采集、选题、稿件、素材管线、发布自检、数据闭环、Agent 编排、配置管理

**待办**（详见 [13-后续开发待办清单](./docs/13-后续开发待办清单.md)）：

- 🔴 **M6 半自动发布** —— 阻塞：需小红书账号扫码登录
- 🔴 **接真实生图 API** —— 现为本地占位实现
- 🔴 **长尾词质量过滤** —— 实测出「天高速公路新」这类垃圾词
- 🟡 去 AI 味、定时任务、技能库（SOP）、对标数据库、生产鉴权

---

## 十一、安全说明

- **密钥**：Fernet 加密存储于 `backend/data/secrets.json`（已 gitignore），查询只返掩码
- **代理**：默认 SQLite（零配置），生产建议 PostgreSQL
- **鉴权**：⚠️ **当前接口无鉴权，不可直接暴露公网**
- **合规**：不爬取平台数据、不自动发布、AI 内容主动标识
