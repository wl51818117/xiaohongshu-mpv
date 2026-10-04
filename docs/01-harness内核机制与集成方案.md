# Harness 内核机制梳理与工作台集成方案

> 调研对象：`E:\Ai-workbuddy\提取harness`
> 调研方式：全量阅读源码 + 本机实测 `/healthz`、`/v1/tools`
> 结论可信度：源码级+ 实测级（非文档推断）

---

## 一、内核本质：一个"零客户端"的 Agent 运行时

### 1.1 它到底是什么

`dsh`（DeepSeek Harness）本质是一个 **cordis 插件树**。所谓"提取内核"，做的是三件事：

| 动作 | 做法 | 结果 |
|---|---|---|
| 只加载内核bundle | profile 的 `bundles` 只写 `@deepseek-ai/dsh-base` | 无任何 UI、无客户端 |
| 自己写宿主 | 插入 `./host/index.js` 插件，提供 HTTP+SSE | 对外有HTTP 接口 |
| 自己写工具 | 插入 `./tools/index.js` 插件，`ctx.tools.register(...)` | 模型可调业务能力 |

`package.json` 是整个"提取"动作的证据：

```json
"dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base"], "patchReload": "startup" } }
```

**一行不改内核代码。** 内核与客户端在官方设计里本就是两个独立 bundle，官方还自带5 个无客户端 profile 模板（`headless` / `sdk` / `sdk-minimal` / `acp`）。

### 1.2 三个关键角色

```
你的应用（界面）
   │  HTTP + SSE
   ▼
① kernel-host        内核宿主（host/index.js）        127.0.0.1:8787
   ↓  ctx.agents / agent.followup
② dsh-base 内核      思考、决策、调工具
   ↑  ctx.tools.register
③ kernel-tools       业务工具（tools/index.js）
```

- **kernel-host**：只做4 件事 —— 把 HTTP 请求转成 Agent 输入、把内核事件转成 SSE、管会话生命周期、承接审批请求。**零UI 逻辑**。
- **kernel-tools**：把业务接口注册成模型可调用的工具。这是扩展能力的**唯一核心挂钩点**。

---

## 二、Agent 调度机制（重点）

### 2.1 调度单位：session = agent

宿主内部维护 `Map<sessionId, {agent, subs}>`：

```js
// host/index.js  ensureAgent()
if (existing) { await existing.agent.whenIdle(); return existing }   // 复用：等当前轮跑完
const { agent } = await ctx.agents.create({
  sessionId: brandString(sessionId),
  agentOptions: { provider, model },          // ← 来自 agentDefaultModel
  setup: (agentCtx) => { installModelSelection(agentCtx, {...}) },
})
```

**调度语义（重要，决定了工作台怎么设计）：**

1. **一个 sessionId 对应一个常驻 Agent**，Agent 内部是有状态的（上下文、记忆都在 session 里）。
2. **并发策略是"排队+ 等待"**，不是抢占：`agent.followup()` 追加一轮，然后 `await agent.whenIdle()`。同一 session 的并发请求会串行执行。
3. **不同 session 完全并行** —— 这是工作台多任务并行的基础。**每个内容任务应该独立开一个 sessionId**。
4. **模型的粒度是per-session 的**：`agentOptions` 在 `agents.create` 时固定。要换模型得开新 session。

> **踩坑警告（源码注释里明确写了）**：`agents.create({ setup })` 的返回值会被agent-loop 当作 commit 载体调用（`(await setup?.(...))?.commit()`）。所以 `setup` **必须用大括号函数体、不许return**，否则启动即抛 `commit is not a function`。

### 2.2 事件流：内核 → SSE

宿主订阅三类内核事件，转成 SSE 推给客户端：

| 内核事件 | SSE event |用途 |
|---|---|---|
| `session/event` 中的 `turn/start`,`turn/end`,`step/start`,`step/end`,`assistant/message`,`tool/call`,`tool/result` | 同名（`/` 换成 `-`） | 回合/步骤边界、工具调用轨迹 |
| `agent/assistant-stream` 的 `text-delta` | `text-delta` | 逐字渲染 |
| `agent/assistant-stream` 的 `reasoning-delta` | `reasoning-delta` | 思维链（前端默认不显示） |
| `approval/request`（waterfall） | `approval-request` | 授权卡片 |

### 2.3 审批闸门（fail-closed）

内核执行需授权工具前会 waterfall 出 `approval/request`。宿主逻辑：

```js
if (!record || record.subs.size === 0) return next()   // 无客户端在线 → 交回内核默认策略
return new Promise(resolve => {
  const timer = setTimeout(() => resolve('rejected'), 120_000)   // 2 分钟无人应答 = 拒绝
  pendingAppers.set(id, { resolve, timer })
})
```

**「没有客户端在线就不擅自放行」** —— 这是安全默认值。工作台必须保持这个语义：批处理任务若无人值守，宁可失败也不能自动放行高危工具。

---

## 三、技能注册 / 调用机制

### 3.1 内核自带 26 个工具（本机实测全通）

`GET /v1/tools` 实测结果：

```
counts: { builtin: 26, custom: 2, declaredBuiltin: 26, declaredCustom: 2 }
未注册: (无)
```

| 类别 | 工具 |
|---|---|
| 执行 | `pwsh` |
| 文件 | `read` `write` `edit` `read_image` |
| 搜索 | `glob` `grep` |
| 联网 | `web_search` `web_fetch` |
| 技能 | `skill` |
| 任务 | `todo_write` |
| **子 Agent** | `subagent` `subagent_fork` `send_message` `interrupt_agent` `list_agents` |
| 后台任务 | `job_output` `job_list` `job_kill` |
| 目标 | `create_goal` `get_goal` `update_goal` |
| 交互 | `ask_user_question` |
| 交付 | `present` |
| 迭代 | `ralph` `workflow` |

**关键资产：`subagent` + `job_*` + `skill` 三件套。**
- `subagent`：派活给子 agent，可并行
- `job_*`：管长时任务（生图/生视频是分钟级的，必须走后台任务）
- `skill`：按需加载 SOP，不占常驻上下文

### 3.2 五种加能力的方式（成本递增）

| # | 方式 | 成本 | 适用 |
|---|---|---|---|
| 1 | 内置工具直接用 | 0 | 搜网页、读文件、跑命令 |
| 2 | 点亮未启用内置工具 | 一行 YAML | 如 `dsh-tool-pwsh-persistent` |
| 3 | **注册业务工具** | 十几行 JS | **工作台的主路径** |
| 4 | 挂 MCP server | 一段配置 | 接外部系统（ERP/DB） |
| 5 | 写技能 skill | 一份 MD | 把 SOP 固化 |

### 3.3 业务工具注册契约（工作台要复刻的形状）

```js
ctx.tools.register({
  name: 'xhs_fetch_hot_topics',
  description: '按赛道词抓取小红书近7天爆款选题。当用户要"找选题""看看最近什么火"时使用。',
  parameters: { type:'object', additionalProperties:false, properties: {...}, required:[...] },
  output: { schema: {...}, render: (args, value) => [{type:'text', text:'...'}] },
  async execute(args, exec) { /* 调工作台 API */ },
})
```

四个硬约束：
1. `description` 是模型**唯一**的判断依据 → 必须写"**什么时候该用它**"，不能只写功能
2. `execute` 返回值必须满足 `output.schema`，否则内核判违规
3. `register()` 返回 disposer，可运行时热插拔工具集
4. 改完要同步 `TOOL_NAMES` 数组（供 `/v1/tools` 自检）

### 3.4 技能挂载现状

`cordis.patch.yml` 里技能目录指向 `E:\智能体\skills`（当前有11 个 md：每日复盘、目标规划、笔记提取等）。

⚠️ **已踩过的坑（start-all.js 里已写自动修复）**：
profile 里新增一行 `dsh-skill-filesystem` 会与 dsh-base 已有的 `skill-filesystem` **抢同一个 provider 名"filesystem"**，导致内核拒绝启动。start-all.js 会检测 `micro-progress-skill-filesystem` 并生成临时 patch 叠加修复，不动原文件。**永久修法是改成覆盖 base 的那一行。**

---

## 四、工作台如何与内核集成

### 4.1 集成拓扑

```
┌─────────────────────────────────────────────┐
│  电商工作台（浏览器）                          │
│  选题库看板 / 文案编辑 / 素材库 / 发布队列      │
└──────────────────┬──────────────────────────┘
                   │ HTTP + SSE（经自己后端转发）
┌──────────────────▼──────────────────────────┐
│  工作台后端（Node/NestJS）                    │
│  · 业务 API（选题/稿件/素材/发布）              │
│  · Agent 网关：/agent/chat 鉴权·限流·审计        │
│  · 工具注册层：把业务 API 包装成内核工具          │
└──────────────────┬──────────────────────────┘
                   │ 127.0.0.1:8787
┌──────────────────▼──────────────────────────┐
│  dsh 内核 8787                                │
│  · subagent 并行调度                          │
│  · skill 按需加载 SOP                          │
│  · job_* 管理生图/生视频长任务                   │
│  · approval 审批闸门（fail-closed）             │
└─────────────────────────────────────────────┘
```

**铁律（web/README 明确警告）：绝不让浏览器直连内核。**
内核带着 `pwsh` / `write` 工具，直连等于把 shell 暴露给前端。必须走自己的后端转发（SSE 关闭缓冲）。

### 4.2 三条集成路径的分工

| 工作台能力 | 集成方式 | 具体挂什么 |
|---|---|---|
| 选题采集、文案生成、打回重写 | **注册业务工具**（方式3） | `xhs_*` 系列 12-15 个工具 |
| 端到端 SOP（如"冷启动 checklist"） | **写技能**（方式5） | `skills/xhs-coldstart.md` 等 |
| 素材库、订单、商品库 | **挂 MCP**（方式4） | 若已有 MCP server |

### 4.3 建议注册的工具清单（工作台核心资产）

| 工具名 | 用途 | 触发语义（description 关键） |
|---|---|---|
| `xhs_fetch_hot_topics` | 抓爆款选题入库 | "找选题""最近什么火" |
| `xhs_search_keywords` | 搜下拉词/热度 | "这个关键词有流量吗" |
| `xhs_list_topics` | 查选题库 | "选题库里有什么" |
| `xhs_claim_topic` | 占用选题→建稿 | "就做这个选题" |
| `xhs_write_copy` | 生成文案初稿 | "写这篇笔记的正文" |
| `xhs_rewrite_copy` | 去AI 味/换风格 | "这稿太AI 了" |
| `xhs_gen_images` | 生封面+配图 | "给这篇配图" |
| `xhs_gen_video` | 图生视频 | "把图转成视频" |
| `xhs_build_post` | 组合成发布包 | "组装这篇笔记" |
| `xhs_publish_draft` | 投递到发布队列 | "准备发布" |
| `xhs_publish_status` | 查发布状态 | "发出去了吗" |
| `xhs_fetch_metrics` | 回流数据复盘 | "这篇数据怎么样" |

**设计要点**：工具粒度按"内容流水线环节"切，而不是按"数据表"切。模型一次对话可以串起 `xhs_claim_topic` → `xhs_write_copy` → `xhs_gen_images` → `xhs_build_post`，这就是 agent 编排替代人工串流程的价值。

### 4.4 一个 session 的生命周期（对应一篇笔记）

```
用户在工作台点"生成这篇笔记"
  → 后端 sessionId = `note-{topicId}`   ← 一个内容任务一个 session
  → POST /v1/chat  {text: "选题#123 做图文笔记，走标准流程"}
  → 内核自主串起：skill(xhs-article-sop) → claim → write_copy → gen_images → build_post
  → SSE 实时回推：文本 / 工具轨迹 / 生图进度
  → done
```

**为什么用 `note-{topicId}` 而非随机 UUID**：
- 可断线续传（刷新页面继续同一会话）
- 可复现（同一选题重跑，Agent 记得上次的判断）
- 天然隔离并发（不同笔记互不阻塞）

### 4.5 权限与审批配置

| 场景 | `DSH_PERMISSION_MODE` | 说明 |
|---|---|---|
| 生产默认 | `workspace-write` | 可写工作目录，需审批 |
| 只读分析（复盘/看数据） | `read-only` | 减少审批打断 |
| **禁止** | `danger-full-access` | 工作台绝不用，等于给所有内容任务 shell 全权 |

发布类工具（`xhs_publish_draft`）应设计为**写库+ 入队**，绝不直接调平台接口 —— 把不可逆动作（真发布）交给人，把可逆动作交给 agent。

---

## 五、harness 现状核实结论

| 项 | 状态 |
|---|---|
| 内核服务 | ✅ 运行中，`127.0.0.1:8787` |
| 生效模型 | `deepseek-official / deepseek-flash` |
| 内置工具 | ✅ 26/26 全部注册成功 |
| 自定义工具 | ✅ 2 个（`echo`、`demo_query_order`，均为示例） |
| 会话 | 4 个活跃 |
| 自定义业务工具 | ❌ 尚无（`tools/index.js` 里还是示例代码） |
| API Key | ✅ 有效（start-all.js 体检通过） |

**唯一待办**：把 `tools/index.js` 的示例工具替换成上表的 `xhs_*` 真实工具。

---

## 六、内核踩坑清单（已验证，设计时要避开）

| 现象 | 原因 | 对策 |
|---|---|---|
| `(intermediate value)?.commit is not a function` | `agents.create({setup})` 的返回值被当commit 载体 | setup 用大括号函数体、不 return |
| `cannot set property "x" without provide` | cordis 里 `ctx.set` 需先 `ctx.provide` | 模块常量导出替代 |
| `cannot get property "tools" without inject` | cordis 代理强制 | 服务名加进 `export const inject = [...]` |
| 内核拒绝启动（provider 名冲突） | 重复挂载 `dsh-skill-filesystem` | 改base 的 skill-filesystem 行 |
| 单条路由报错拖垮整个进程 | `createServer` 回调无兜底 | try/catch 包住路由分发 |
| 凭据不生效 | 环境变量 > `.credentials.yaml` | 用 `setx` 统一 |
