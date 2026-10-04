/**
 * 工作台能力声明：让 Agent 能操作界面。
 *
 * 这是 hbridge v2.1 的核心用法 —— 执行体留在浏览器，
 * 模型调用时内核经 SSE 把调用推回来，我们在自己的 JS 上下文里执行。
 *
 * 能拿到什么：React 状态、当前页签、DOM。
 * 拿不到：后端数据（那走已有的 xhs_* 工具）。
 *
 * 边界（重要）：
 *  - 这里只做「界面导航 + 视图筛选」，不直接写数据库
 *  - 任何破坏性动作都必须人工确认
 */

import type { HarnessBridge } from './harness-bridge'

/** 工作台暴露给 Agent 的操作集合 */
export type WorkbenchOps = {
  /** 切换页签 */
  gotoTab: (tab: string) => void
  /** 设置选题筛选 */
  setTopicFilter: (filter: { status?: string; keyword?: string }) => void
  /** 选中某条选题 */
  selectTopic: (topicId: number) => void
  /** 打开 Agent 侧栏 */
  openAgent: () => void
  /** 当前视图状态读取 */
  readView: () => ViewState
}

/** 工作台当前视图状态（Agent 读它就知道用户在哪儿） */
export type ViewState = {
  tab: string
  topicFilter: { status?: string; keyword?: string }
  selectedTopicId: number | null
  stats: { materials: number; topics: number; drafts: number }
}

const TABS = [
  'flow',
  'pipeline',
  'materials',
  'topics',
  'draft',
  'assets',
  'publish',
  'analytics',
] as const

/**
 * 建立 bridge 连接并注册工作台能力。
 *
 * @param ops 工作台操作集合（由 App 传入，闭包住 React 状态）
 * @param onEvent 事件回调（用于把 state-delta 等回传到界面）
 */
export async function connectWorkbench(
  ops: WorkbenchOps,
  onEvent?: (type: string, data: unknown) => void,
) {
  const api = (window as unknown as { HarnessBridge: typeof HarnessBridge })
    .HarnessBridge

  if (!api) {
    throw new Error('HarnessBridge SDK 未加载')
  }

  // ── 声明能力 ────────────────────────────────────────
  // description 是模型判断「该不该调用」的唯一依据，必须写清触发场景。
  const capabilities = [
    api.defineCapability({
      name: 'navigate_workbench',
      title: '切换工作台页面',
      description:
        '切换电商工作台当前显示的页面。当用户说"看看选题库""去素材库""打开流水线"' +
        '"我想看数据""打开流程图"这类指向某个功能区的请求时使用。' +
        '参数 tab 填目标页面名称，可选值：flow 流程图 / pipeline 流水线 / ' +
        'materials 素材库 / topics 选题库 / draft 稿件 / assets 素材工坊 / ' +
        'publish 发布队列 / analytics 数据看板。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          tab: {
            type: 'string',
            description: '要切换到的页面名称',
            enum: [...TABS],
          },
        },
        required: ['tab'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            tab: { type: 'string' },
          },
          required: ['ok', 'tab'],
        },
      },
      annotations: { destructiveHint: false, idempotentHint: true },
      handler: async ({ tab }: { tab: string }) => {
        const ok = TABS.includes(tab as (typeof TABS)[number])
        if (ok) ops.gotoTab(tab)
        return { ok, tab: ok ? tab : '' }
      },
    }),

    api.defineCapability({
      name: 'filter_topics',
      title: '筛选选题',
      description:
        '按状态或关键词筛选选题库。当用户说"只看已发布的选题""找找关于某个词的选题"' +
        '"把没写稿的筛出来"时使用。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          status: {
            type: 'string',
            description: '按状态筛选，留空表示不按状态过滤',
            enum: ['', 'pooled', 'claimed', 'done', 'archived'],
          },
          keyword: {
            type: 'string',
            description: '标题关键词过滤，留空表示不过滤',
          },
        },
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            status: { type: 'string' },
            keyword: { type: 'string' },
          },
          required: ['ok', 'status', 'keyword'],
        },
      },
      annotations: { destructiveHint: false, idempotentHint: true },
      handler: async ({ status, keyword }: { status?: string; keyword?: string }) => {
        ops.gotoTab('topics')
        ops.setTopicFilter({ status: status || undefined, keyword: keyword || undefined })
        return { ok: true, status: status ?? '', keyword: keyword ?? '' }
      },
    }),

    api.defineCapability({
      name: 'open_topic',
      title: '打开某条选题',
      description:
        '打开指定 id 的选题并跳转到稿件页开始写稿。当用户说"打开第 3 条选题"' +
        '"就做那个手机银行的选题"时使用。先用 list_topics 拿到 id。',
      parameters: {
        type: 'object',
        additionalProperties: false,
        properties: {
          topicId: { type: 'integer', description: '选题 id' },
        },
        required: ['topicId'],
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            topicId: { type: 'integer' },
            title: { type: 'string' },
          },
          required: ['ok', 'topicId'],
        },
      },
      annotations: { destructiveHint: false, idempotentHint: true },
      handler: async ({ topicId }: { topicId: number }) => {
        ops.selectTopic(topicId)
        ops.gotoTab('draft')
        return { ok: true, topicId, title: '' }
      },
    }),

    api.defineCapability({
      name: 'read_workbench_state',
      title: '读取工作台当前状态',
      description:
        '读取工作台当前所在的页面、筛选条件与数据统计。' +
        '当需要知道"用户现在在看什么""当前有多少条素材"时使用。' +
        '这是只读操作，随时可调。',
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
            tab: { type: 'string' },
            topicFilter: {
              type: 'object',
              additionalProperties: false,
              properties: {
                status: { type: 'string' },
                keyword: { type: 'string' },
              },
            },
            selectedTopicId: { type: 'integer' },
            stats: {
              type: 'object',
              additionalProperties: false,
              properties: {
                materials: { type: 'integer' },
                topics: { type: 'integer' },
                drafts: { type: 'integer' },
              },
              required: ['materials', 'topics', 'drafts'],
            },
          },
          required: ['tab', 'topicFilter', 'stats'],
        },
      },
      annotations: { readOnlyHint: true, idempotentHint: true },
      handler: async () => ops.readView(),
    }),
  ]

  // ── 接入 ───────────────────────────────────────────
  const bridge = await api.connect({
    // 走自己的后端转发 —— 内核绝不能被浏览器直连（它带 shell 类工具）
    baseUrl: '/api/bridge',
    appId: 'workbench',
    capabilities,
    // 票据由后端签发，前端永远拿不到静态凭据。
    // 返回 { token, expiresAt } 让 SDK 自己缓存并按时续签。
    tokenProvider: async () => {
      const resp = await fetch('/api/bridge/ticket', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ appId: 'workbench' }),
      })
      if (!resp.ok) throw new Error('票据签发失败')
      const data = await resp.json()
      return { token: data.ticket as string, expiresAt: Number(data.expiresAt) || 0 }
    },
  })

  // 上下文：告诉 Agent 用户当前在哪个页面
  bridge.setContext({ route: location.pathname, page: ops.readView().tab })

  if (onEvent) {
    bridge.on('state-delta', (data: unknown) => onEvent('state-delta', data))
  }

  await bridge.whenSynced()
  return bridge
}
