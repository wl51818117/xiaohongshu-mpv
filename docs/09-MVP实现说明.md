# 电商工作台 · MVP 说明

> 依据 `docs/08-终极选型方案v4单账号版.md` 提取的最小可运行主流程。
> 原则：**只做一条能跑通的链路，剥离一切非必需功能。**

---

## 一、MVP 做什么（功能范围）

### ✅ 已实现：一条完整主流程

```
RSS 源 ──> ① 采集入库 ──> ② AI选题转换 ──> ③ 选题库
                                      ↓
                              ④ 合规过筛（硬闸）
```

| 环节 | 说明 | 对应文档 |
|---|---|---|
| ① RSS 采集 | feedparser 抓公开 RSS 源，清洗 HTML，内容指纹去重 | docs/06 |
| ② 选题转换 | 换视角 / 落人群 / 提长尾词 / 标差异化维度 | docs/07 |
| ③ 选题库 | 分类（价值类型）、状态机、溯源（关联源与素材） | docs/03 |
| ④ 合规过筛 | 医疗/金融/法律/减肥/绝对化用语黑名单，命中即拒 | docs/00 |
| ⑤ 内核对接 | Agent 网关 SSE 透传 + 审批通道 + 工具清单 | docs/01 |

### ❌ 刻意剥离（后续阶段再做）

| 未做 | 原因 | 计划阶段 |
|---|---|---|
| 自有账号爆款回采 | 需接 XHS-Downloader MCP + 登录态 | P2 |
| 文案生成与去 AI 味 | 需内核工具注册 | P3 |
| 图片/视频素材生成 | 需接 guizang skill + AI Provider | P4 |
| 发布代理 | 需接 xhs_ai_publisher | P5 |
| 数据回流看板 | 需发布后才有数据 | P7 |

**剥离理由**：这四块都依赖外部项目或长时任务，放在 MVP 会让「能不能跑通」这个问题变得不可验证。先证明数据链路通，再逐层加能力。

---

## 二、技术栈（与文档一致）

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | FastAPI + Pydantic 2 | 文档定：AI 编排是主复杂度，Python 生态优势 |
| 数据层 | SQLAlchemy 2 + SQLite | **MVP 用 SQLite 零依赖**；生产改 PostgreSQL 只改 `DATABASE_URL` |
| RSS | feedparser | 文档定：Python 生态首选 |
| 内核 | dsh harness @ 8787 | 本机已在运行 |
| 前端 | **Vite 6 + React 19 + TS + Tailwind 4** | 前后端分离，`/api` 代理到 8000 |

---

## 三、启动与验证

### 方式零：一键启动前后端（推荐）

```bash
# 项目根目录双击
start-all.bat
```

自动启动后端(8000) + 前端(5173) 并打开浏览器。
**前端界面：<http://127.0.0.1:5173>**

### 方式一：后端单启（Windows）

```bash
cd backend
start_mvp.bat          # 双击也行
```

脚本会自动：建venv → 装依赖 → 初始化数据库 → 启动服务。

### 方式二：手动三步

```bash
# 1. 建环境 + 装依赖
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. 启动
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/docs> 看接口文档。

### 最小用例（3 步跑通）

```bash
# ① 查看初始状态
curl http://127.0.0.1:8000/api/pipeline/status

# ② 采集 RSS（每源取 3 条，快速验证）
curl -X POST http://127.0.0.1:8000/api/pipeline/collect \
  -H "content-type: application/json" \
  -d '{"limit_per_feed":3}'

# ③ 转成选题
curl -X POST "http://127.0.0.1:8000/api/pipeline/convert?limit=20"

# ④ 看结果
curl http://127.0.0.1:8000/api/topics
curl "http://127.0.0.1:8000/api/pipeline/status"
```

**预期**：②返回 `added > 0`（外网可达时）；④能看到选题，含长尾词、人群、差异化维度。

### 验证内核对接（可选，需内核在跑）

```bash
curl http://127.0.0.1:8000/api/agent/kernel
```

返回 `ok: true` 且 `tools` 有 26 个内置工具即通过。

---

## 四、目录结构

```
backend/
├─ app/
│  ├─ main.py              应用入口
│  ├─ core/config.py       集中配置
│  ├─ db/
│  │  ├─ models.py         数据模型（4 张表）
│  │  └─ session.py        会话管理
│  ├─ services/
│  │  ├─ rss_collector.py  采集服务
│  │  └─ topic_converter.py 选题转换（含合规黑名单）
│  ├─ agent/
│  │  └─ kernel.py         dsh 内核客户端
│  └─ api/
│     ├─ pipeline.py       主流程接口
│     └─ agent.py          Agent 对话接口
├─ skills/                 Agent 技能（后续阶段）
├─ tests/                  测试
├─ data/                   SQLite 数据库（gitignore）
├─ requirements.txt
└─ .env.example
```

---

## 五、关键设计决策

| 决策 | 理由 |
|---|---|
| **SQLite 而非 PostgreSQL** | MVP 要「零依赖跑通」；`DATABASE_URL` 一改即可切生产 |
| **合规黑名单在M1 就拦** | 高风险题材（医疗/荐股/法律）越早拦越省，避免生成后才发现 |
| **RSS 源做成配置化** | 赛道待定（docs/08 待确认 #2），改配置即可换源，不用改代码 |
| **启发式替代 LLM 做选题转换** | 保证内核未启动时也能独立跑通；生产接内核后换LLM 精度更高 |
| **内核客户端容忍失败** | 内核未启动时返回 `ok: false` 而非崩溃，降级可用 |
| **前端暂缓** | 先验证数据链路；前端是P5+ 的事|

---

## 六、已知限制

| 限制 | 影响 | 后续 |
|---|---|---|
| 选题转换用启发式而非 LLM | 长尾词抽取精度一般 | P3 接内核 LLM 改造 |
| 默认 RSS 源是科技/商业 | 与最终赛道可能不符 | 换源即可（配置化）|
| 无鉴权 | 不能暴露公网 | 生产必须加 |
| 无 Alembic 迁移 | 表结构改了只能删库重建 | 生产引入 Alembic |

---

## 七、下一步（P2-P3）

优先级最高的两块：

1. **接内核做选题转换**（P3）
   注册 `xhs_convert_topic` 工具，让 LLM 做长尾词抽取和差异化规划，替代启发式。

2. **接自有爆款回采**（P2）
   挂 XHS-Downloader 的 MCP，实现「自己账号爆款 → 要素提炼 → 新选题」。

详见 `docs/08-终极选型方案v4单账号版.md` 的实施路线。
