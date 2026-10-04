import { useCallback, useEffect, useState } from 'react'
import { type Draft, type Validation } from '../lib/api'
import { DiffBadge, EmptyState, SectionTitle, Tag, ValueTypeBadge } from './ui'

/** 打磨动作标签（与后端 POLISH_ACTIONS 对应）*/
const POLISH_LABELS: Record<string, string> = {
  humanize: '去 AI 味',
  polish: '润色',
  shorten: '精简',
  expand: '扩写',
  hook: '改开头',
}
const POLISH_ACTIONS = [
  { key: 'humanize', label: '去 AI 味' },
  { key: 'polish', label: '润色' },
  { key: 'hook', label: '改开头' },
  { key: 'shorten', label: '精简' },
  { key: 'expand', label: '扩写' },
]

type TopicContext = {
  topic_id: number
  title: string
  keyword_target: string
  persona: string
  value_type: string
  differentiation: string[]
  status: string
  has_draft: boolean
  writing_brief: string
}

/** 选题写作台。
 *
 * ★ 流程修正（2026-10-04）：稿件不是独立页，而是**选题的下游**。
 *   - 必须从选题库「写稿」进入，本页不提供独立新建
 *   - 左侧常驻选题上下文（长尾词/人群/差异化），写稿时随时可见
 *   - 顶部「AI 生成」按写作简报生成初稿，生成后可手动改
 */
export function WritingDesk({
  topicId,
  draftId,
  onNotify,
  onChanged,
  onBack,
}: {
  topicId: number | null
  draftId: number | null
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onChanged: () => void
  onBack: () => void
}) {
  const [ctx, setCtx] = useState<TopicContext | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [tagsText, setTagsText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [validation, setValidation] = useState<Validation | null>(null)
  // AI 能力
  const [titleOptions, setTitleOptions] = useState<string[]>([])
  const [tagOptions, setTagOptions] = useState<string[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLog, setChatLog] = useState<{ role: 'user' | 'ai'; text: string }[]>([])

  const load = useCallback(async () => {
    if (!topicId) return
    try {
      const c = await fetch(`/api/drafts/topic-context/${topicId}`).then((x) => x.json())
      setCtx(c)
      if (draftId) {
        const d = await fetch(`/api/drafts/${draftId}`).then((x) => x.json())
        setDraft(d)
        setTitle(d.title || '')
        setBody(d.body || '')
        setTagsText((d.tags || []).join('、'))
        setValidation(d.validation)
      }
    } catch {
      onNotify('err', '加载选题上下文失败')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topicId, draftId])

  useEffect(() => {
    void load()
  }, [load])

  const generate = async () => {
    if (!draftId) return
    setBusy('ai')
    try {
      const r = await fetch('/api/drafts/generate-copy', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ draft_id: draftId }),
      }).then((x) => x.json())
      if (!r.ok) {
        onNotify('err', typeof r.detail === 'string' ? r.detail : '生成失败')
        return
      }
      setBody(r.body)
      setValidation(r.validation)
      onNotify(
        r.validation.passed ? 'ok' : 'err',
        r.validation.passed
          ? `已生成 ${r.body.length} 字，校验通过`
          : `已生成 ${r.body.length} 字，还有 ${r.validation.spec_issues.length + r.validation.compliance_issues.length} 项待修`,
      )
      onChanged()
    } catch (e) {
      onNotify('err', `生成失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const save = async () => {
    if (!draftId) return
    setBusy('save')
    try {
      const r = await fetch('/api/drafts', {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          id: draftId,
          title,
          body,
          tags: tagsText.split(/[、,，\s]+/).filter(Boolean),
        }),
      }).then((x) => x.json())
      setValidation(r.validation)
      const issues =
        r.validation.spec_issues.length + r.validation.compliance_issues.length
      onNotify(
        issues === 0 ? 'ok' : 'err',
        issues === 0 ? `已保存，校验通过（${r.validation.score} 分）` : `已保存，还有 ${issues} 项待修`,
      )
      onChanged()
    } catch (e) {
      onNotify('err', `保存失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }


  /** AI 批量出 10 个爆款标题，用户挑选。 */
  const genTitles = async () => {
    if (!ctx) return
    setBusy('titles')
    try {
      const r = await fetch('/api/ai/titles', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          topic: ctx.title,
          keyword: ctx.keyword_target,
          persona: ctx.persona,
          value_type: ctx.value_type,
          n: 10,
        }),
      }).then((x) => x.json())
      if (r.titles?.length) {
        setTitleOptions(r.titles)
        onNotify('ok', `已生成 ${r.count} 个标题候选，点一个即可采用`)
      } else {
        onNotify('err', '未生成出标题，请重试')
      }
    } catch (e) {
      onNotify('err', `生成标题失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  /** 智能标签推荐。 */
  const genTags = async () => {
    setBusy('tags')
    try {
      const r = await fetch('/api/ai/tags', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ title, body, persona: ctx?.persona || '' }),
      }).then((x) => x.json())
      if (r.tags?.length) {
        setTagOptions(r.tags)
        onNotify('ok', `推荐 ${r.count} 个标签，点一下加入`)
      } else {
        onNotify('err', '未获取到标签建议')
      }
    } catch (e) {
      onNotify('err', `标签推荐失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  /** 单次打磨：去AI味 / 润色 / 精简 / 扩写 / 改开头。 */
  const polish = async (action: string) => {
    setBusy(action)
    try {
      const r = await fetch('/api/ai/polish', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ body, action, keyword: ctx?.keyword_target || '' }),
      }).then((x) => x.json())
      if (r.body) {
        setBody(r.body)
        if (r.validation) setValidation(r.validation)
        onNotify('ok', `${POLISH_LABELS[action] || '打磨'}完成（${r.body.length} 字）`)
      }
    } catch (e) {
      onNotify('err', `打磨失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  /** 多轮对话打磨。 */
  const sendChat = async () => {
    const msg = chatInput.trim()
    if (!msg || !body) return
    setChatInput('')
    setChatLog((l) => [...l, { role: 'user', text: msg }])
    setBusy('chat')
    try {
      const r = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ body, message: msg, keyword: ctx?.keyword_target || '' }),
      }).then((x) => x.json())
      if (r.body) {
        setBody(r.body)
        if (r.validation) setValidation(r.validation)
        setChatLog((l) => [...l, { role: 'ai', text: `已按要求改（${r.body.length} 字）` }])
      }
    } catch (e) {
      onNotify('err', `对话失败：${(e as Error).message}`)
      setChatLog((l) => [...l, { role: 'ai', text: `失败：${(e as Error).message}` }])
    } finally {
      setBusy('')
    }
  }

  if (!topicId || !ctx) {
    return (
      <>
        <SectionTitle title="选题写作台" />
        <EmptyState
          title="请先从选题库选一条选题"
          hint="稿件必须从选题派生 —— 在选题库点「写稿」进入"
        />
      </>
    )
  }

  const bodyLen = body.length

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-3">
        <button className="btn-ghost btn-sm" onClick={onBack}>
          ← 返回选题库
        </button>
        <div className="flex items-center gap-2">
          <button
            className="btn-accent btn-sm"
            disabled={busy !== null}
            onClick={() => void generate()}
          >
            {busy === 'ai' ? '生成中…' : body ? 'AI 重新生成' : 'AI 生成正文'}
          </button>
          <button
            className="btn-primary btn-sm"
            disabled={busy !== null}
            onClick={() => void save()}
          >
            {busy === 'save' ? '保存中…' : '保存'}
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        {/* ── 左：选题上下文（常驻，写稿时随时可见） ── */}
        <div className="space-y-3">
          <div className="card p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-stone-500">所属选题</span>
              {draft && <Tag tone="purple">稿件 #{draft.id}</Tag>}
            </div>
            <div className="text-sm font-medium leading-snug text-stone-900">
              {ctx.title}
            </div>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              <ValueTypeBadge value={ctx.value_type} />
              {ctx.persona && <Tag tone="sky">{ctx.persona}</Tag>}
            </div>
          </div>

          <div className="card p-4">
            <div className="mb-2 text-xs font-medium text-stone-500">
              目标长尾词
            </div>
            <div className="rounded-lg bg-orange-50 px-2.5 py-1.5 font-mono text-xs text-orange-800 dark:bg-orange-500/10 dark:text-orange-300">
              {ctx.keyword_target || '（未提取）'}
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-stone-400">
              必须出现在标题前 8-13 字与正文前 80 字
            </p>
          </div>

          {ctx.differentiation.length > 0 && (
            <div className="card p-4">
              <div className="mb-2 text-xs font-medium text-stone-500">
                差异化要求（至少 3 项）
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ctx.differentiation.map((d) => (
                  <DiffBadge key={d} label={d} />
                ))}
              </div>
            </div>
          )}

          {/* 写作简报 */}
          <details className="card p-4">
            <summary className="cursor-pointer text-xs font-medium text-stone-500">
              写作简报（喂给 AI 用）
            </summary>
            <pre className="mt-2 whitespace-pre-wrap text-[11px] leading-relaxed text-stone-600 dark:text-stone-300">
              {ctx.writing_brief}
            </pre>
          </details>
        </div>

        {/* ── 右：正文编辑 ── */}
        <div className="space-y-3">
          <div className="card p-5">
            <div className="mb-3 flex items-center justify-between">
              <label className="field-label !mb-0">标题</label>
              <span
                className={`text-xs tabular-nums ${
                  title.length > 20 ? 'text-red-600' : 'text-stone-400'
                }`}
              >
                {title.length}/20
              </span>
            </div>
            <div className="mb-2 flex gap-2">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="field flex-1"
                placeholder="≤20 字，长尾词放前 8-13 字"
              />
              <button
                className="btn-accent shrink-0"
                disabled={busy !== null}
                onClick={() => void genTitles()}
              >
                {busy === 'titles' ? '生成中…' : 'AI 出 10 个标题'}
              </button>
            </div>

            {/* 标题候选：用户自己挑 */}
            {titleOptions.length > 0 && (
              <div className="mb-4 rounded-lg bg-zinc-50 p-2.5 dark:bg-zinc-800/60">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[11px] font-medium text-stone-500">
                    标题候选（点一个采用）
                  </span>
                  <button
                    className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
                    onClick={() => setTitleOptions([])}
                  >
                    收起
                  </button>
                </div>
                <div className="grid gap-1">
                  {titleOptions.map((t, i) => (
                    <button
                      key={i}
                      onClick={() => {
                        setTitle(t)
                        setTitleOptions([])
                        onNotify('ok', '已采用该标题')
                      }}
                      className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors ${
                        t === title
                          ? 'bg-orange-100 font-medium text-orange-800 dark:bg-orange-500/20 dark:text-orange-200'
                          : 'text-stone-700 hover:bg-stone-200 dark:text-stone-200 dark:hover:bg-zinc-700'
                      }`}
                    >
                      <span className="shrink-0 text-[10px] text-stone-400">
                        {i + 1}
                      </span>
                      <span className="truncate">{t}</span>
                      <span className="ml-auto shrink-0 text-[10px] text-stone-400">
                        {t.length}字
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="mb-3 flex items-center justify-between">
              <label className="field-label !mb-0">正文</label>
              <span
                className={`text-xs tabular-nums ${
                  bodyLen < 300 || bodyLen > 800
                    ? 'text-amber-600'
                    : 'text-stone-400'
                }`}
              >
                {bodyLen}/300-800
              </span>
            </div>
            {/* 打磨工具条：AI 多轮修改 */}
            {body && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-stone-400">AI 打磨：</span>
                {POLISH_ACTIONS.map((a) => (
                  <button
                    key={a.key}
                    className="btn-ghost !min-h-[26px] !px-2 !py-0.5 !text-[11px]"
                    disabled={busy !== null}
                    onClick={() => void polish(a.key)}
                  >
                    {busy === a.key ? '处理中…' : a.label}
                  </button>
                ))}
              </div>
            )}

            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={14}
              placeholder={
                body
                  ? ''
                  : '点右上角「AI 生成正文」自动生成初稿，或在此手写\n\n要求：长尾词出现在前 80 字，三段式（痛点 → 干货 → 互动）'
              }
              className="field resize-y leading-relaxed"
            />

            <div className="mt-4">
              <div className="mb-1.5 flex items-center justify-between">
                <label className="field-label !mb-0">标签（3-5 个）</label>
                <button
                  className="btn-accent !min-h-[26px] !px-2 !py-0.5 !text-[11px]"
                  disabled={busy !== null}
                  onClick={() => void genTags()}
                >
                  {busy === 'tags' ? '推荐中…' : '智能推荐标签'}
                </button>
              </div>
              <input
                value={tagsText}
                onChange={(e) => setTagsText(e.target.value)}
                className="field"
                placeholder="穿搭、通勤、上班族"
              />

              {/* 标签候选：点一下加入 */}
              {tagOptions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {tagOptions.map((t) => {
                    const has = tagsText
                      .split(/[、,，\s]+/)
                      .includes(t)
                    return (
                      <button
                        key={t}
                        onClick={() => {
                          const cur = tagsText.split(/[、,，\s]+/).filter(Boolean)
                          const next = has
                            ? cur.filter((x) => x !== t)
                            : [...cur, t]
                          setTagsText(next.join('、'))
                        }}
                        className={`tag transition-colors ${
                          has
                            ? 'bg-orange-100 text-orange-800 dark:bg-orange-500/25 dark:text-orange-200'
                            : 'bg-stone-100 text-stone-600 hover:bg-stone-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700'
                        }`}
                      >
                        {t}
                        {has ? ' ✓' : ' +'}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

          {/* 多轮打磨对话 */}
          {body && (
            <div className="card p-4">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs font-medium text-stone-600 dark:text-stone-300">
                  多轮打磨
                </span>
                <span className="text-[11px] text-stone-400">
                  试：「再口语一点」「换个开头」「加一段真实经历」
                </span>
              </div>

              {chatLog.length > 0 && (
                <div className="mb-2 max-h-32 space-y-1.5 overflow-y-auto">
                  {chatLog.map((m, i) => (
                    <div
                      key={i}
                      className={`text-xs ${
                        m.role === 'user' ? 'text-stone-600' : 'text-emerald-700'
                      }`}
                    >
                      <span className="font-medium">
                        {m.role === 'user' ? '你：' : 'AI：'}
                      </span>
                      {m.text}
                    </div>
                  ))}
                </div>
              )}

              <div className="flex gap-2">
                <input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void sendChat()
                    }
                  }}
                  placeholder="告诉 AI 想怎么改…"
                  className="field flex-1 !min-h-[34px] !py-1.5 !text-xs"
                />
                <button
                  className="btn-primary shrink-0 !min-h-[34px] !px-3 !py-1.5 !text-xs"
                  disabled={busy !== null || !chatInput.trim()}
                  onClick={() => void sendChat()}
                >
                  {busy === 'chat' ? '处理中' : '发送'}
                </button>
              </div>
            </div>
          )}

          {/* 校验面板 */}
          {validation && (
            <div className="card p-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-stone-500">
                  规格校验
                </span>
                <span
                  className={`text-sm font-semibold tabular-nums ${
                    validation.passed
                      ? 'text-emerald-600'
                      : 'text-amber-600'
                  }`}
                >
                  {validation.score}
                </span>
              </div>
              {validation.passed ? (
                <p className="text-xs text-emerald-700">
                  ✓ 全部符合平台规格，可进入素材工坊
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {[
                        ...validation.compliance_issues.map((i) => ({
                          t: i,
                          c: 'red' as const,
                        })),
                        ...validation.spec_issues.map((i) => ({
                          t: i,
                          c: 'amber' as const,
                        })),
                      ].map((x, i) => (
                        <li
                          key={i}
                          className={`flex gap-1.5 text-xs ${
                            x.c === 'red'
                              ? 'text-red-700 dark:text-red-300'
                              : 'text-amber-700 dark:text-amber-300'
                          }`}
                        >
                          <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-current" />
                          {x.t}
                        </li>
                      ))}
                  </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
