/** 后端 API 客户端。
 *
 * 开发时通过 vite 代理转发到 8000，避免跨域。
 * 所有请求走同源 /api 前缀——内核绝不能被前端直连（安全铁律）。
 */

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`${resp.status} ${text.slice(0, 200)}`)
  }
  return resp.json() as Promise<T>
}

// ── 类型 ──────────────────────────────────────────────

export type PipelineStatus = {
  materials: number
  topics: number
  unconverted_materials: number
  available_topics: number
}

export type Topic = {
  id: number
  title: string
  keyword_target: string
  persona: string
  value_type: string
  differentiation: string[]
  status: string
  source_name: string
}

export type Material = {
  id: number
  source_name: string
  source_type: string
  title: string
  own_flag: boolean
  has_topic: boolean
}

export type CollectResult = {
  ok: boolean
  fetched: number
  added: number
  duplicated: number
  per_feed: { name: string; fetched: number; added: number }[]
}

export type ConvertResult = {
  ok: boolean
  processed: number
  converted: number
  rejected: number
  reject_reasons: string[]
}

export type KernelStatus = {
  ok: boolean
  error?: string
  model?: { provider?: string; model?: string }
  sessions?: number
  tools?: { name: string; source?: string; registered?: boolean }[]
}

// ── 稿件相关类型 ───────────────────────────────────────

export type Validation = {
  passed: boolean
  spec_issues: string[]
  compliance_issues: string[]
  score: number
  stats?: {
    title_len: number
    body_len: number
    tag_count: number
    keyword_hits: number
  }
}

export type Draft = {
  id: number
  topic_id: number | null
  pipeline_type: string
  title: string
  body: string
  tags: string[]
  cover_url?: string
  images?: string[]
  ai_declaration: string
  validation: Validation
  topic_title?: string
  keyword?: string
}

export type DraftListItem = {
  id: number
  topic_id: number | null
  pipeline_type: string
  title: string
  tags: string[]
  ai_declaration: string
  validation: Validation
  topic_title: string
  keyword: string
  updated_at: string | null
}

// ── 接口 ──────────────────────────────────────────────

export const api = {
  status: () => request<PipelineStatus>('/pipeline/status'),

  collect: (limitPerFeed = 3) =>
    request<CollectResult>('/pipeline/collect', {
      method: 'POST',
      body: JSON.stringify({ limit_per_feed: limitPerFeed }),
    }),

  convert: (limit = 20) =>
    request<ConvertResult>(`/pipeline/convert?limit=${limit}`, { method: 'POST' }),

  topics: (limit = 100) =>
    request<{ count: number; items: Topic[] }>(`/topics?limit=${limit}`),

  materials: (limit = 100) =>
    request<{ count: number; items: Material[] }>(`/materials?limit=${limit}`),

  kernel: () => request<KernelStatus>('/agent/kernel'),

  // ── 稿件 ──
  drafts: (limit = 50) =>
    request<{ count: number; items: DraftListItem[] }>(`/drafts?limit=${limit}`),

  draft: (id: number) => request<Draft>(`/drafts/${id}`),

  createDraft: (payload: {
    topic_id?: number | null
    pipeline_type?: string
    title?: string
    body?: string
    tags?: string[]
  }) =>
    request<{ ok: boolean; id: number; validation: Validation }>('/drafts', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateDraft: (payload: {
    id: number
    title?: string
    body?: string
    tags?: string[]
    ai_declaration?: string
  }) =>
    request<{ ok: boolean; id: number; validation: Validation }>('/drafts', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deleteDraft: (id: number) =>
    request<{ ok: boolean }>(`/drafts/${id}`, { method: 'DELETE' }),

  /** 实时校验，不保存 */
  validateDraft: (payload: {
    title: string
    body: string
    tags: string[]
    keyword: string
    ai_declaration: string
    pipeline_type?: string
  }) => request<Validation>('/drafts/validate', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
}

/** Agent 对话：SSE 流式读取。
 *
 * 注意：绝不直连内核，必须经后端 /api/agent/chat 转发。
 */
export async function streamChat(
  text: string,
  sessionId: string | undefined,
  onEvent: (event: string, data: unknown) => void,
): Promise<void> {
  const resp = await fetch('/api/agent/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text, session_id: sessionId }),
  })
  if (!resp.body) throw new Error('响应无body')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 以空行分隔事件块
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''

    for (const part of parts) {
      let event = 'message'
      let data = ''
      for (const line of part.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data))
        } catch {
          /* 忽略非JSON 片段 */
        }
      }
    }
  }
}
