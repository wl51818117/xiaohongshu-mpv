import { useCallback, useState } from 'react'
import { Button, EmptyState, SectionTitle, Tag } from './ui'

/** 工作台 —— 唯一的主入口。
 *
 * ★ 为什么要有这个页面（2026-10-05 收口）：
 *   原来有 12 个平级页签，用户打开不知道从哪开始——
 *   他自己的反馈是「采集回来的信息不能加入选题库」「流程上是有问题的」。
 *   调研 GitHub 上跑通的小红书工具（16k★ 的 xiaohongshu-mcp 只有 8 个工具、
 *   5.6k★ 的 RedInk 交 15 张 PNG），**没有一个交「一个工作台」**。
 *
 *   所以这里只做**一个动作**，5 步走完，每步都能看见结果：
 *
 *     ① 粘贴链接（或手打一段原文）
 *        ↓
 *     ② 拆解：标题公式 / 痛点 / 场景
 *        ↓
 *     ③ 确认选题（可改标题与核心词）
 *        ↓
 *     ④ 生成稿件
 *        ↓
 *     ⑤ 导出/发布
 *
 *   关键：**② ③ 是一屏内完成的**，不是让用户在 5 个页面间跳。
 */

type Step = 1 | 2 | 3 | 4

type Break = {
  title_patterns: string[]
  hook: string
  pains: { name: string; quote: string }[]
  scenes: string[]
  reuse: { borrowed: string; must_change: string }
  core_words: string[]
}

export function Workbench({
  onNotify,
  onDraftCreated,
}: {
  onNotify: (k: 'ok' | 'err', m: string) => void
  /** 稿件建好后跳转过去 */
  onDraftCreated: (draftId: number) => void
}) {
  const [step, setStep] = useState<Step>(1)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [br, setBr] = useState<Break | null>(null)
  const [topicTitle, setTopicTitle] = useState('')
  const [topicKeyword, setTopicKeyword] = useState('')
  const [topicId, setTopicId] = useState<number | null>(null)
  const [draftId, setDraftId] = useState<number | null>(null)
  const [saved, setSaved] = useState<{ title: string; body: string; tags: string[] } | null>(null)

  const reset = useCallback(() => {
    setStep(1)
    setInput('')
    setBr(null)
    setTopicTitle('')
    setTopicKeyword('')
    setTopicId(null)
    setDraftId(null)
    setSaved(null)
  }, [])

  // ── ② 拆解：把粘贴的内容变成结构化分析 ──
  const analyze = async () => {
    const text = input.trim()
    if (!text) {
      onNotify('err', '先粘贴一个链接或一段原文')
      return
    }
    setBusy(true)
    try {
      // 链接与纯文本走同一个存素材的接口，只是 url/title 不同
      const isUrl = /^https?:\/\//.test(text)
      const noteId = isUrl
        ? text.match(/\/explore\/([a-zA-Z0-9]+)/)?.[1] ||
          text.split('/').pop()?.split('?')[0] ||
          ''
        : ''
      const collect = await fetch('/api/browser/collect', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          url: isUrl ? text : `manual://${Date.now()}`,
          title: isUrl
            ? `采集的笔记 ${noteId}`
            : text.slice(0, 40),
          content: text,
        }),
      })
      if (!collect.ok) {
        const err = await collect.json().catch(() => ({}) as { detail?: string })
        onNotify('err', `采集失败：${err.detail || collect.status}`)
        return
      }
      const cd = await collect.json()
      const matId = cd.id as number

      // 分析
      const r = await fetch(`/api/materials/${matId}/analyze`, { method: 'POST' })
      const d = await r.json()
      const a = d.analysis || {}
      const ta = a.title_analysis || {}
      const ca = a.content_analysis || {}
      const ra = a.reuse_advice || {}

      setBr({
        title_patterns: ta.ai_patterns?.length ? ta.ai_patterns : ta.patterns || [],
        hook: ta.ai_hook || '',
        pains: (ca.pains || []).map((p: { name: string; quote: string }) => ({
          name: p.name,
          quote: p.quote,
        })),
        scenes: ca.scenes || [],
        reuse: {
          borrowed: ra.borrowed || '',
          must_change: ra.must_change || '',
        },
        core_words: ta.core_words || [],
      })
      setStep(2)
    } catch (e) {
      onNotify('err', `拆解失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  // ── ③ 确认选题 → 创建 ──
  const createTopic = async () => {
    setBusy(true)
    try {
      // 找到刚才那条素材
      const list = await fetch('/api/materials?limit=1').then((x) => x.json())
      const matId = list.items?.[0]?.id
      if (!matId) {
        onNotify('err', '找不到素材，请重新采集')
        return
      }
      const r = await fetch(`/api/materials/${matId}/to-topic`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          material_id: matId,
          title: topicTitle,
          keyword: topicKeyword,
          use_ai: false, // 已分析过，不必再花一次调用
        }),
      })
      const d = await r.json()
      if (!d.ok) {
        onNotify('err', d.detail || '创建选题失败')
        return
      }
      if (!d.created) {
        onNotify('err', d.reason || '选题已存在')
        return
      }
      setTopicId(d.id)
      setStep(3)
      onNotify('ok', `选题已创建：${d.title}`)
    } catch (e) {
      onNotify('err', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // ── ④ 生成稿件 ──
  const createDraft = async () => {
    if (!topicId) return
    setBusy(true)
    try {
      // 选题 → 稿件
      const r = await fetch('/api/drafts/from-topic', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ topic_id: topicId }),
      })
      const d = await r.json()
      if (!d.id) {
        onNotify('err', d.detail || '建稿失败')
        return
      }
      // AI 生成正文 + 标签
      const g = await fetch('/api/drafts/generate-copy', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ draft_id: d.id }),
      })
      const gd = await g.json()
      if (!g.ok) {
        onNotify('err', gd.detail || '生成正文失败')
        return
      }
      setDraftId(d.id)
      setSaved({
        title: gd.body ? '' : '',
        body: gd.body || '',
        tags: gd.tags || [],
      })
      setStep(4)
      onNotify('ok', '稿件已生成')
    } catch (e) {
      onNotify('err', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <SectionTitle
        title="工作台"
        desc="粘贴一篇爆款链接，5 步拿到一篇能发的稿子"
        action={
          step > 1 && (
            <Button variant="ghost" size="sm" onClick={reset}>
              重新开始
            </Button>
          )
        }
      />

      {/* 步骤条 */}
      <div className="mb-5 flex items-center gap-1.5">
        {(['粘贴爆款', '拆解结构', '确认选题', '生成稿件'] as const).map((label, i) => {
          const n = (i + 1) as Step
          const done = step > n
          const cur = step === n
          return (
            <div key={label} className="flex items-center gap-1.5">
              <div
                className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] transition-colors ${
                  cur
                    ? 'bg-brand-500 text-white'
                    : done
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-stone-100 text-stone-400'
                }`}
              >
                <span className="tabular-nums">{done ? '✓' : n}</span>
                <span>{label}</span>
              </div>
              {i < 3 && <span className="text-stone-300">→</span>}
            </div>
          )
        })}
      </div>

      {/* ① 粘贴 */}
      {step === 1 && (
        <div className="card p-6">
          <div className="mb-3">
            <label className="field-label">粘贴一篇你觉得做得好的小红书链接</label>
            <textarea
              className="field resize-y"
              rows={4}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                'https://www.xiaohongshu.com/explore/xxxxx\n\n也可以直接把手抄的正文粘进来'
              }
            />
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="primary"
              size="md"
              loading={busy}
              onClick={analyze}
            >
              开始拆解
            </Button>
            <span className="text-[11px] text-stone-400">
              拆解会分析标题用了什么结构、正文里有哪些痛点
            </span>
          </div>

          <div className="mt-5 rounded-[var(--r-md)] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2.5 text-[11px] leading-relaxed text-stone-500">
            <strong className="font-medium text-stone-600">为什么不做「自动采集」？</strong>
            <br />
            因为自动采集来的内容你多半不会认真看。我们只处理**你主动挑中的那一篇**。
            浏览器扩展装好后，在小红书页面上点右下角「＋采集」也能把内容送进来。
          </div>
        </div>
      )}

      {/* ② 拆解结果 */}
      {step === 2 && br && (
        <div className="space-y-3">
          <div className="card p-5">
            <div className="mb-2 text-[13px] font-medium text-stone-800 dark:text-stone-100">
              它是怎么抓住人的
            </div>
            {br.title_patterns.length > 0 ? (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {br.title_patterns.map((p) => (
                  <Tag key={p} tone="purple">
                    {p}
                  </Tag>
                ))}
              </div>
            ) : (
              <div className="text-[12px] text-stone-400">
                没识别出明显的标题结构
              </div>
            )}
            {br.hook && (
              <p className="text-[12px] leading-relaxed text-stone-600 dark:text-stone-300">
                {br.hook}
              </p>
            )}
          </div>

          {(br.pains.length > 0 || br.scenes.length > 0) && (
            <div className="card p-5">
              <div className="mb-2 text-[13px] font-medium text-stone-800 dark:text-stone-100">
                它戳中的是什么
              </div>
              <div className="flex flex-wrap gap-1.5">
                {br.pains.map((p) => (
                  <Tag key={p.name} tone="rose">
                    {p.name}
                  </Tag>
                ))}
                {br.scenes.map((s) => (
                  <Tag key={s} tone="sky">
                    {s}
                  </Tag>
                ))}
              </div>
              {br.pains[0]?.quote && (
                <p className="mt-2 text-[11px] text-stone-400">
                  用户原话：{br.pains[0].quote}
                </p>
              )}
            </div>
          )}

          {br.reuse.borrowed && (
            <div className="card p-5">
              <div className="mb-2 text-[13px] font-medium text-stone-800 dark:text-stone-100">
                我们该学什么、不能学什么
              </div>
              {br.reuse.borrowed && (
                <p className="text-[12px] leading-relaxed text-stone-600 dark:text-stone-300">
                  <span className="font-medium text-emerald-600">可借鉴：</span>
                  {br.reuse.borrowed}
                </p>
              )}
              {br.reuse.must_change && (
                <p className="mt-1.5 text-[12px] leading-relaxed text-stone-600 dark:text-stone-300">
                  <span className="font-medium text-amber-600">必须改：</span>
                  {br.reuse.must_change}
                </p>
              )}
            </div>
          )}

          <div className="card p-5">
            <div className="mb-3 text-[13px] font-medium text-stone-800 dark:text-stone-100">
              确认选题
            </div>
            <div className="space-y-2">
              <div>
                <label className="field-label">选题标题（可改）</label>
                <input
                  className="field"
                  value={topicTitle}
                  onChange={(e) => setTopicTitle(e.target.value)}
                  placeholder="留空则用分析结果自动生成"
                />
              </div>
              <div>
                <label className="field-label">核心搜索词（必填）</label>
                <input
                  className="field"
                  value={topicKeyword}
                  onChange={(e) => setTopicKeyword(e.target.value)}
                  placeholder="用户会搜的词，如「一次性内裤 差旅」"
                />
                <p className="mt-1 text-[11px] text-stone-400">
                  建议 2-6 字。空关键词的稿件无法通过校验
                </p>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2">
              <Button
                variant="primary"
                size="md"
                loading={busy}
                disabled={!topicKeyword.trim()}
                onClick={createTopic}
              >
                {topicKeyword.trim() ? '创建选题' : '先填核心搜索词'}
              </Button>
              <Button variant="ghost" size="md" onClick={reset}>
                重新选一篇
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ③ 建稿 */}
      {step === 3 && (
        <div className="card p-6">
          <EmptyState
            title="选题已就绪"
            hint="下一步生成正文与标签，产出可发布的稿子"
          />
          <div className="mt-4 flex justify-center">
            <Button
              variant="primary"
              size="md"
              loading={busy}
              onClick={createDraft}
            >
              生成稿件
            </Button>
          </div>
        </div>
      )}

      {/* ④ 完成 */}
      {step === 4 && (
        <div className="card p-6">
          <div className="mb-3 flex items-center gap-2">
            <span className="text-base">✓</span>
            <div>
              <div className="text-sm font-medium text-stone-800 dark:text-stone-100">
                稿件已生成
              </div>
              <div className="text-[11px] text-stone-400">
                正文 {saved?.body?.length ?? 0} 字 · 标签 {saved?.tags?.length ?? 0} 个
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="md"
              onClick={() => draftId && onDraftCreated(draftId)}
            >
              去编辑与发布
            </Button>
            <Button variant="default" size="md" onClick={reset}>
              再做一篇
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
