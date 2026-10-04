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
