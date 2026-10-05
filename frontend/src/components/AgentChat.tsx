import { useEffect, useRef, useState } from 'react'
import { streamChat } from '../lib/api'

type Msg = {
  role: 'user' | 'assistant'
  text: string
}

/** Agent 对话侧栏。
 *
 * 布局约定：
 *  - 高度 100%，由父容器（App 侧栏）决定，与页面等高
 *  - 消息区 flex-1 + overflow-y-auto，内部独立滚动，不撑高页面
 *  - 输入区固定在底部（shrink-0），不随消息滚动
 *
 * 安全铁律：绝不浏览器直连内核——只调后端 /api/agent/chat。
 */
export function AgentChat({
  online,
  onNotify,
  onClose,
}: {
  online: boolean
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onClose?: () => void
}) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bufRef = useRef('')
  const scrollRef = useRef<HTMLDivElement>(null)
  // 会话 id 用量：sessionStart 是「打开面板的时刻」，sessionSeq 逐轮递增。
  // 组合起来保证「同一轮内稳定、跨轮唯一」。
  const sessionStart = useRef(String(Date.now()))
  const sessionSeq = useRef(0)

  // 新消息到达时滚到底
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs, busy])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return

    setInput('')
    setMsgs((m) => [...m, { role: 'user', text }, { role: 'assistant', text: '' }])
    setBusy(true)
    bufRef.current = ''
    sessionSeq.current += 1

    // ── 会话 id：每轮对话唯一 ──
    // ★原来硬编码 'note-demo'，导致：
    //   内核 dsh-session 的 create() 在同名 session 存在时直接抛错
    //   （lib/index.js:1380 `session "x" already exists`），
    //   于是第二次发消息就报「session already exists」。
    //
    // 为什么不用固定 id（断线续传的价值）：
    //   这里每轮是独立问答，不是持续对话；复用同一 id 反而会
    //   让上一轮的上下文污染这一轮。
    //
    // 为什么不用纯随机：
    //   同一轮的重试/断线重连需要同一个 id 才能续上，
    //   所以用「打开面板时的起始时间 + 递增序号」——
    //   同一轮内保持稳定，新的一轮自动换新。
    const sessionId = `chat-${sessionSeq.current}-${sessionStart.current}`

    // 收集本轮的真实错误原因。没拿到就只能在气泡里给一句空话，
    // 那等于让用户猜（踩过：401 被显示成「内核未返回文本」）。
    let errDetail = ''

    try {
      await streamChat(text, sessionId, (event, data) => {
        if (event === 'text-delta') {
          const chunk = (data as { text?: string })?.text ?? ''
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
          const d = data as { message?: string; detail?: string; hint?: string }
          errDetail = [d?.message, d?.detail, d?.hint].filter(Boolean).join('\n')
          onNotify('err', d?.message ?? '内核返回错误')
        }
      })

      if (!bufRef.current) {
        const fallback = errDetail
          ? `⚠️ ${errDetail}`
          : '（内核未返回文本。若上方无错误提示，多为模型仍在思考或工具调用被卡住，可稍后重试或换个说法。）'
        setMsgs((m) => {
          const next = [...m]
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].role === 'assistant') {
              next[i] = { ...next[i], text: next[i].text || fallback }
              break
            }
          }
          return next
        })
      }
    } catch (e) {
      onNotify('err', `对话失败：${(e as Error).message}`)
      setMsgs((m) => {
        const next = [...m]
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === 'assistant') {
            next[i] = { ...next[i], text: `⚠️ 对话失败：${(e as Error).message}` }
            return next.slice(0, i + 1)
          }
        }
        return next
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      {/* 侧栏头部 */}
      <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] bg-[var(--surface-2)] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-stone-900 dark:text-stone-50">Agent</span>
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              online ? 'bg-emerald-500' : 'bg-stone-300'
            }`}
          />
          <span className="text-xs text-stone-400">
            {online ? '在线' : '离线'}
          </span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            aria-label="收起 Agent 侧栏"
            className="rounded-lg p-1.5 text-stone-400 transition-colors
                       hover:bg-stone-200/60 hover:text-stone-700 dark:hover:bg-zinc-800"
          >
            {/* 收起图标：右向箭头 */}
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M6 3.5L10.5 8L6 12.5"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
      </div>

      {/* 消息区：flex-1 + 独立滚动 */}
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4 bg-[var(--surface)]"
      >
        {msgs.length === 0 && (
          <div className="py-8 text-center">
            <div className="text-sm text-stone-500">问我任何关于内容运营的事</div>
            <div className="mt-1 text-xs leading-relaxed text-stone-400">
              我会调用内核的工具
              <br />
              查选题、看素材
            </div>
          </div>
        )}

        {msgs.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                m.role === 'user'
                  ? 'bg-gradient-to-br from-[var(--grad-purple-from)] to-[var(--grad-purple-to)] text-white shadow-sm'
                  : 'border border-[var(--border)] bg-[var(--surface-2)] text-stone-800'
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}

        {busy && (
          <div className="flex items-center gap-1.5 text-xs text-stone-400">
            <span className="flex gap-0.5">
              <span className="h-1 w-1 animate-bounce rounded-full bg-stone-400 [animation-delay:-0.3s]" />
              <span className="h-1 w-1 animate-bounce rounded-full bg-stone-400 [animation-delay:-0.15s]" />
              <span className="h-1 w-1 animate-bounce rounded-full bg-stone-400" />
            </span>
            内核思考中，首个响应可能需十几秒
          </div>
        )}
      </div>

      {/* 输入区：固定底部，不参与滚动 */}
      <div className="shrink-0 border-t border-[var(--border)] bg-[var(--surface-2)] p-3">
        {!online && (
          <div className="mb-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs leading-relaxed text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            内核未启动。到
            <code className="mx-1">提取harness</code>
            目录双击 start-all.cmd。
          </div>
        )}
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
            placeholder={online ? '输入问题，回车发送' : '内核离线'}
            disabled={!online || busy}
            className="min-w-0 flex-1 rounded-lg border border-stone-300 px-2.5 py-2
                       text-sm outline-none placeholder:text-stone-400
                       focus:border-stone-500 disabled:bg-stone-50"
          />
          <button
            className="btn-primary shrink-0 !px-3"
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
