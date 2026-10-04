import { useRef, useState } from 'react'
import { streamChat } from '../lib/api'

type Msg = {
  role: 'user' | 'assistant' | 'system'
  text: string
}

/** Agent 对话窗。
 *
 * 铁律：绝不浏览器直连内核——这里只调后端 /api/agent/chat，
 * 由后端转发 SSE 并负责鉴权与限流。
 */
export function AgentChat({
  online,
  onNotify,
}: {
  online: boolean
  onNotify: (kind: 'ok' | 'err', msg: string) => void
}) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bufRef = useRef('')

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return

    setInput('')
    setMsgs((m) => [...m, { role: 'user', text }])
    setBusy(true)
    bufRef.current = ''

    // 会话约定：note-{topicId}（docs/01）。此处用固定 demo 会话。
    const sessionId = 'note-demo'

    // 先插一个空的 assistant 气泡，边流边追加
    setMsgs((m) => [...m, { role: 'assistant', text: '' }])

    try {
      await streamChat(text, sessionId, (event, data) => {
        if (event === 'text-delta') {
          const d = data as { text?: string }
          const chunk = d?.text ?? ''
          if (!chunk) return
          bufRef.current += chunk
          const snapshot = bufRef.current
          setMsgs((m) => {
            const next = [...m]
            for (let i = next.length - 1; i >= 0; i--) {
              if (next[i].role === 'assistant') {
                next[i] = { ...next[i], text: snapshot }
                break
              }
            }
            return next
          })
        } else if (event === 'error') {
          const d = data as { message?: string }
          onNotify('err', d?.message ?? '内核返回错误')
        }
      })

      // 若内核没吐任何文本，给个提示
      if (!bufRef.current) {
        setMsgs((m) => {
          const next = [...m]
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].role === 'assistant') {
              next[i] = {
                ...next[i],
                text: next[i].text || '（内核未返回文本，可能是工具调用或模型无响应）',
              }
              break
            }
          }
          return next
        })
      }
    } catch (e) {
      onNotify('err', `对话失败：${(e as Error).message}`)
      setMsgs((m) => m.slice(0, -1))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card flex h-[calc(100vh-220px)] flex-col overflow-hidden">
      {/* 消息区 */}
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {msgs.length === 0 && (
          <div className="py-10 text-center">
            <div className="text-sm text-stone-500">问我任何关于内容运营的事</div>
            <div className="mt-1 text-xs text-stone-400">
              我会调用内核的工具来查选题、看素材
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
                m.role === 'user'
                  ? 'bg-stone-900 text-white'
                  : 'border border-stone-200 bg-stone-50 text-stone-800'
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}
        {busy && (
          <div className="text-xs text-stone-400">
            内核思考中…（首个响应可能需十几秒）
          </div>
        )}
      </div>

      {/* 输入区 */}
      <div className="border-t border-stone-200 p-3">
        {!online && (
          <div className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
            内核未启动，对话不可用。到
            <code>E:\Ai-workbuddy\提取harness</code> 双击 start-all.cmd 启动。
          </div>
        )}
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && void send()}
            placeholder={online ? '输入问题，回车发送' : '内核离线'}
            disabled={!online || busy}
            className="flex-1 rounded-lg border border-stone-300 px-3 py-2 text-sm
                       outline-none placeholder:text-stone-400
                       focus:border-stone-500 disabled:bg-stone-50"
          />
          <button
            className="btn-primary"
            onClick={() => void send()}
            disabled={!online || busy || !input.trim()}
          >
            {busy ? '发送中' : '发送'}
          </button>
        </div>
      </div>
    </div>
  )
}
