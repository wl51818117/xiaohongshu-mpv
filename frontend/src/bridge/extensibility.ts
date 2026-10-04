/**
 * Agent 运行时能力框架 —— 让 Agent 能自己「改」工作台。
 *
 * ══ 为什么需要这个 ══
 * hbridge v2.1 的核心是「执行体留在客户端，模型调用推回浏览器执行」。
 * 但能力若只在编译期固化，Agent 就只能用它做事的被限制：
 * 想要新能力，得改代码 → 重新构建 → 等编译。
 *
 * 正确做法（hbridge 原文）：
 *   bridge.register(capabilities)  —— 运行时动态注册。
 *
 * 分层：
 *   L1 原子能力  本文件提供（导航/表单/面板/数据读写/注册新能力）
 *   L2 业务能力  Agent 用 L1 原子能力注册，运行时生效、持久化
 *   L3 界面结构  Agent 动态组织（增删面板、改布局）
 */

import type { Bridge } from './harness-bridge'

const STORAGE_KEY = 'wb-agent-capabilities'
const PANELS_KEY = 'wb-agent-panels'

/** 动态面板（Agent 可增删改） */
export type DynamicPanel = {
  id: string
  title: string
  kind: 'list' | 'detail' | 'metrics' | 'note'
  /** 数据来源接口路径，或直接内联数据 */
  source?: string
  data?: unknown
  note?: string
  createdBy: 'agent' | 'user'
  createdAt: string
}

type Persisted = {
  panels: DynamicPanel[]
  /** Agent 自定义能力名列表（实际执行体在运行时重建） */
  customSkills: { name: string; title: string; note: string; createdAt: string }[]
}

function load(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const p = JSON.parse(raw)
      return { panels: p.panels ?? [], customSkills: p.customSkills ?? [] }
    }
  } catch {
    /* 损坏则重建 */
  }
  return { panels: [], customSkills: [] }
}

function save(p: Persisted) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(p))
  localStorage.setItem(PANELS_KEY, JSON.stringify(p.panels.map((x) => x.id)))
}

/** 工作台数据访问（供 Agent 读写） */
async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json() as Promise<T>
}

async function apiSend<T>(
  path: string,
  method: 'POST' | 'PATCH' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) {
    const t = await r.text()
    throw new Error(`${path} → HTTP ${r.status}: ${t.slice(0, 160)}`)
  }
  return r.json() as Promise<T>
}

/** 声明 L1 原子能力。
 *
 * description 是模型判断「该不该调用」的唯一依据，
 * **必须写清触发场景**，并把枚举值列出来（否则模型会猜参数名）。
 */
export function defineMetaCapabilities(
  bridge: Bridge,
  ctx: {
    /** 切换页签 */
    goto: (tab: string) => void
    /** 打开 Agent 面板 */
    openPanel: (panelId: string) => void
    /** 刷新工作台数据 */
    refresh: () => void
    /** 当前可用页签（喂给模型做枚举约束） */
    tabs: { key: string; label: string }[]
  },
) {
  const tabs = ctx.tabs
  const tabHint = tabs.map((t) => `${t.key}(${t.label})`).join(' / ')

  return [
    bridge && {
      name: 'ui_add_panel',
      title: '新增界面面板',
      description:
        '在工作台里动态新增一个面板，不需要改代码。' +
        '当用户说「加一个 XX 面板/页面」「我想看 XX 数据」「加个对比表」时使用。' +
        'panelKind 可选：list 列表 / detail 详情 / metrics 指标 / note 文本说明。' +
        `source 可填后端接口路径（如 /api/topics），留空则用 inlineData 直接传数据。`,
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          panelId: { type: 'string', description: '面板唯一 id，如 topic-compare' },
          title: { type: 'string', description: '面板标题' },
          panelKind: {
            type: 'string',
            description: '面板类型',
            enum: ['list', 'detail', 'metrics', 'note'],
          },
          source: {
            type: 'string',
            description: '后端接口路径，可空',
          },
          inlineData: { type: 'string', description: '直接内联的数据(JSON 字符串)，可空' },
          note: { type: 'string', description: '面板说明文字，可空' },
        },
        required: ['panelId', 'title', 'panelKind'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            panelId: { type: 'string' },
            total: { type: 'integer' },
          },
          required: ['ok', 'panelId'],
        },
      },
      annotations: { destructiveHint: false, idempotentHint: true },
      handler: async (args: any) => {
        const p = load()
        if (p.panels.some((x) => x.id === args.panelId)) {
          return { ok: true, panelId: args.panelId, total: p.panels.length }
        }
        let inline: unknown
        if (args.inlineData) {
          try {
            inline = JSON.parse(String(args.inlineData))
          } catch {
            inline = String(args.inlineData)
          }
        }
        p.panels.push({
          id: String(args.panelId),
          title: String(args.title),
          kind: (args.panelKind ?? 'note') as DynamicPanel['kind'],
          source: args.source || undefined,
          data: inline,
          note: args.note || undefined,
          createdBy: 'agent',
          createdAt: new Date().toISOString(),
        })
        save(p)
        ctx.openPanel(String(args.panelId))
        return { ok: true, panelId: String(args.panelId), total: p.panels.length }
      },
    },

    {
      name: 'ui_remove_panel',
      title: '删除界面面板',
      description:
        '删除之前新增的面板。当用户说「删掉那个面板」「去掉 XX 页面」时使用。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: { panelId: { type: 'string', description: '要删除的面板 id' } },
        required: ['panelId'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            panelId: { type: 'string' },
            total: { type: 'integer' },
          },
          required: ['ok', 'panelId'],
        },
      },
      annotations: { destructiveHint: true, idempotentHint: true },
      handler: async (args: any) => {
        const p = load()
        p.panels = p.panels.filter((x) => x.id !== args.panelId)
        save(p)
        return { ok: true, panelId: String(args.panelId), total: p.panels.length }
      },
    },

    {
      name: 'ui_list_panels',
      title: '查看已建面板',
      description: '列出当前工作台里所有 Agent 新增的面板。用户问「我加了哪些页面」时使用。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {},
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            count: { type: 'integer' },
            panels: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                properties: {
                  id: { type: 'string' },
                  title: { type: 'string' },
                  kind: { type: 'string' },
                },
                required: ['id', 'title', 'kind'],
              },
            },
          },
          required: ['ok', 'count', 'panels'],
        },
      },
      annotations: { readOnlyHint: true, idempotentHint: true },
      handler: async () => {
        const p = load()
        return {
          ok: true,
          count: p.panels.length,
          panels: p.panels.map((x) => ({
            id: x.id,
            title: x.title,
            kind: x.kind as string,
          })),
        }
      },
    },

    {
      name: 'wb_read_data',
      title: '读取工作台数据',
      description:
        '读取工作台任意只读接口的数据。当需要查看选题、稿件、素材、看板数据时使用。' +
        'path 填接口路径，如 /api/topics、/api/analytics/dashboard、/api/publish/package/1。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          path: { type: 'string', description: '后端接口路径，须以 /api/ 开头' },
        },
        required: ['path'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            path: { type: 'string' },
            count: { type: 'integer' },
          },
          required: ['ok', 'path'],
        },
      },
      annotations: { readOnlyHint: true, idempotentHint: true },
      handler: async (args: any) => {
        const path = String(args.path)
        if (!path.startsWith('/api/')) {
          throw new Error('path 必须以 /api/ 开头')
        }
        const data = await apiGet(path)
        const count = Array.isArray(data)
          ? data.length
          : ((data as any)?.count ?? (data as any)?.items?.length ?? 0)
        return { ok: true, path, count: Number(count) || 0 }
      },
    },

    {
      name: 'wb_write_data',
      title: '写入工作台数据',
      description:
        '向工作台写入数据（创建选题、更新稿件等）。' +
        '当用户说「加一条选题」「把这个稿改了」时使用。' +
        'method 选 POST（新建）或 PATCH（更新）。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          path: { type: 'string', description: '接口路径，须以 /api/ 开头' },
          method: {
            type: 'string',
            description: 'HTTP 方法',
            enum: ['POST', 'PATCH'],
          },
          payload: { type: 'string', description: '请求体（JSON 字符串）' },
        },
        required: ['path', 'method'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            path: { type: 'string' },
          },
          required: ['ok', 'path'],
        },
      },
      annotations: { destructiveHint: true, idempotentHint: false },
      handler: async (args: any) => {
        const path = String(args.path)
        if (!path.startsWith('/api/')) throw new Error('path 必须以 /api/ 开头')
        const method = args.method === 'PATCH' ? 'PATCH' : 'POST'
        let body: unknown
        if (args.payload) {
          try {
            body = JSON.parse(String(args.payload))
          } catch {
            throw new Error('payload 不是合法 JSON')
          }
        }
        await apiSend(path, method, body)
        ctx.refresh()
        return { ok: true, path }
      },
    },

    {
      name: 'wb_navigate',
      title: '切换工作台页面',
      description:
        `切换工作台的页面。可选：${tabHint}。` +
        '当用户说「看看选题库」「打开流水线」「去数据看板」时使用。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          tab: { type: 'string', description: '目标页面', enum: tabs.map((t) => t.key) },
        },
        required: ['tab'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: { ok: { type: 'boolean' }, tab: { type: 'string' } },
          required: ['ok', 'tab'],
        },
      },
      annotations: { destructiveHint: false, idempotentHint: true },
      handler: async (args: any) => {
        const t = String(args.tab)
        ctx.goto(t)
        return { ok: true, tab: t }
      },
    },
  ].filter(Boolean) as any[]
}

/** 读取 Agent 建的面板（供 UI 渲染） */
export function readPanels(): DynamicPanel[] {
  return load().panels
}

/** 监听面板变化（跨组件同步） */
export function onPanelsChange(cb: () => void): () => void {
  const handler = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) cb()
  }
  window.addEventListener('storage', handler)
  return () => window.removeEventListener('storage', handler)
}

/** 供 UI 层订阅：面板增删时通知本地组件 */
export function notifyPanelsChanged() {
  window.dispatchEvent(new CustomEvent('wb-panels-changed'))
}
