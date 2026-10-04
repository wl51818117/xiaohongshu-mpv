import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  type Draft,
  type DraftListItem,
  type Topic,
  type Validation,
} from '../lib/api'

/** 稿件编辑器：选题 → 编辑 → 实时校验 → 保存。
 *
 * 校验是确定性的（后端规则引擎），所以边打字边提示，
 * 不需要等 AI 回复——这是最快给出反馈的交互。
 */
export function DraftEditor({
  topics,
  selectedTopicId,
  onNotify,
  onChanged,
}: {
  topics: Topic[]
  selectedTopicId?: number | null
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onChanged: () => void
}) {
  const [drafts, setDrafts] = useState<DraftListItem[]>([])
  const [current, setCurrent] = useState<Draft | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [tagsText, setTagsText] = useState('')
  const [declaration, setDeclaration] = useState('')
  const [topicId, setTopicId] = useState<number | null>(null)
  const [validation, setValidation] = useState<Validation | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  // 可选选题：尚未建稿的
  const availableTopics = useMemo(
    () => topics.filter((t) => t.status === 'pooled' || t.status === 'claimed'),
    [topics],
  )

  const keyword = useMemo(() => {
    if (current?.keyword) return current.keyword
    const t = topics.find((x) => x.id === topicId)
    return t?.keyword_target || ''
  }, [current, topicId, topics])

  const loadDrafts = useCallback(async () => {
    try {
      const r = await api.drafts()
      setDrafts(r.items)
      if (r.items.length > 0 && !current) {
        await openDraft(r.items[0].id)
      }
    } catch (e) {
      onNotify('err', `稿件加载失败：${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current])

  useEffect(() => {
    void loadDrafts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 外部（如 Agent 的 open_topic 能力）指定了选题 → 切到它并预填标题
  useEffect(() => {
    if (!selectedTopicId) return
    setTopicId(selectedTopicId)
    const t = topics.find((x) => x.id === selectedTopicId)
    // 若该选题还没有稿件，新建一篇并预填标题
    if (t && !drafts.some((d) => d.topic_id === selectedTopicId)) {
      setCurrent(null)
      setTitle(t.title.slice(0, 20))
      setBody('')
      setTagsText('')
      setDeclaration('本内容含 AI 辅助生成部分')
      setValidation(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTopicId])

  const openDraft = async (id: number) => {
    try {
      const d = await api.draft(id)
      setCurrent(d)
      setTitle(d.title)
      setBody(d.body)
      setTagsText((d.tags || []).join('、'))
      setDeclaration(d.ai_declaration)
      setTopicId(d.topic_id)
      setValidation(d.validation)
    } catch (e) {
      onNotify('err', `打开失败：${(e as Error).message}`)
    }
  }

  const newDraft = () => {
    setCurrent(null)
    setTitle('')
    setBody('')
    setTagsText('')
    setDeclaration('本内容含 AI 辅助生成部分')
    setTopicId(availableTopics[0]?.id ?? null)
    setValidation(null)
  }

  // 实时校验：停止输入 500ms 后触发，避免每字都请求
  useEffect(() => {
    if (!title && !body) return
    const timer = setTimeout(() => {
      api
        .validateDraft({
          title,
          body,
          tags: tagsText.split(/[、,，\s]+/).filter(Boolean),
          keyword,
          ai_declaration: declaration,
        })
        .then(setValidation)
        .catch(() => {})
    }, 500)
    return () => clearTimeout(timer)
  }, [title, body, tagsText, keyword, declaration])

  const save = async () => {
    setBusy(true)
    try {
      const payload = {
        title,
        body,
        tags: tagsText.split(/[、,，\s]+/).filter(Boolean),
        ai_declaration: declaration,
      }
      if (current) {
        const r = await api.updateDraft({ id: current.id, ...payload })
        setValidation(r.validation)
        onNotify(
          r.validation.passed ? 'ok' : 'err',
          r.validation.passed
            ? `已保存，校验通过（${r.validation.score} 分）`
            : `已保存，但还有 ${r.validation.spec_issues.length + r.validation.compliance_issues.length} 个问题待修正`,
        )
      } else {
        const r = await api.createDraft({
          ...payload,
          topic_id: topicId,
          pipeline_type: 'image',
        })
        onNotify(
          r.validation.passed ? 'ok' : 'err',
          r.validation.passed
            ? `已创建稿件 #${r.id}，校验通过`
            : `已创建稿件 #${r.id}，待完善`,
        )
        const d = await api.draft(r.id)
        setCurrent(d)
      }
      await loadDrafts()
      onChanged()
    } catch (e) {
      onNotify('err', `保存失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!current) return
    setBusy(true)
    try {
      await api.deleteDraft(current.id)
      onNotify('ok', '已删除')
      newDraft()
      await loadDrafts()
      onChanged()
    } catch (e) {
      onNotify('err', `删除失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <div className="py-16 text-center text-sm text-stone-400">加载中…</div>
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr_280px]">
      {/* 左：稿件列表 */}
      <div className="card p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-stone-600">
            稿件 {drafts.length}
          </span>
          <button className="btn-ghost !px-2 !py-1 !text-xs" onClick={newDraft}>
            + 新建
          </button>
        </div>
        {drafts.length === 0 ? (
          <p className="py-6 text-center text-xs text-stone-400">
            还没有稿件
            <br />
            点「+ 新建」开始
          </p>
        ) : (
          <div className="space-y-1">
            {drafts.map((d) => (
              <button
                key={d.id}
                onClick={() => void openDraft(d.id)}
                className={`w-full rounded-lg px-2.5 py-2 text-left transition-colors ${
                  current?.id === d.id
                    ? 'bg-gradient-to-br from-[var(--grad-purple-from)] to-[var(--grad-purple-to)] text-white shadow-sm'
                    : 'hover:bg-stone-100 dark:hover:bg-zinc-800'
                }`}
              >
                <div className="truncate text-xs font-medium">
                  {d.title || '(未命名)'}
                </div>
                <div
                  className={`mt-0.5 flex items-center gap-1 text-[10px] ${
                    current?.id === d.id ? 'text-stone-400' : 'text-stone-400'
                  }`}
                >
                  <span
                    className={`h-1 w-1 rounded-full ${
                      d.validation?.passed ? 'bg-emerald-500' : 'bg-amber-500'
                    }`}
                  />
                  {d.validation?.score ?? 0} 分
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 中：编辑器 */}
      <div className="card p-5">
        {!current && (
          <div className="mb-4">
            <label className="field-label">
              关联选题（建稿后会占用该选题）
            </label>
            <select
              value={topicId ?? ''}
              onChange={(e) => setTopicId(Number(e.target.value) || null)}
              className="field"
            >
              <option value="">不关联</option>
              {availableTopics.map((t) => (
                <option key={t.id} value={t.id}>
                  #{t.id} {t.title.slice(0, 30)}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="mb-3">
          <div className="mb-1 flex items-center justify-between">
            <label className="field-label !mb-0">标题</label>
            <span
              className={`text-xs tabular-nums ${
                title.length > 20 ? 'text-red-600' : 'text-stone-400'
              }`}
            >
              {title.length}/20
            </span>
          </div>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="≤20 字，前 8-13 字含长尾词"
            className="field"
          />
          {keyword && (
            <p className="mt-1 text-xs text-stone-400">
              目标长尾词：
              <span className="text-stone-600">{keyword}</span>
            </p>
          )}
        </div>

        <div className="mb-3">
          <div className="mb-1 flex items-center justify-between">
            <label className="field-label !mb-0">正文</label>
            <span
              className={`text-xs tabular-nums ${
                body.length < 300 || body.length > 800
                  ? 'text-amber-600'
                  : 'text-stone-400'
              }`}
            >
              {body.length}/300-800
            </span>
          </div>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={12}
            placeholder={
              '三段式：开头 50 字直击痛点 → 中间分点给干货 → 结尾总结加互动提问\n前 80 字要出现目标长尾词'
            }
            className="field resize-y leading-relaxed"
          />
        </div>

        <div className="mb-3 grid grid-cols-2 gap-3">
          <div>
            <label className="field-label">
              标签（顿号分隔，3-5 个）
            </label>
            <input
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="穿搭、通勤、上班族"
              className="field"
            />
          </div>
          <div>
            <label className="field-label">
              AI 声明（平台强制）
            </label>
            <input
              value={declaration}
              onChange={(e) => setDeclaration(e.target.value)}
              placeholder="本内容含 AI 辅助生成部分"
              className="field"
            />
          </div>
        </div>

        <div className="flex gap-2 border-t border-stone-200 pt-3">
          <button
            className="btn-accent"
            disabled={busy}
            onClick={() => void save()}
          >
            {busy ? '保存中…' : current ? '保存修改' : '创建稿件'}
          </button>
          {current && (
            <button
              className="btn-ghost"
              disabled={busy}
              onClick={() => void remove()}
            >
              删除
            </button>
          )}
        </div>
      </div>

      {/* 右：实时校验 */}
      <div className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs font-medium text-stone-600">实时校验</span>
          {validation && (
            <span
              className={`text-sm font-semibold tabular-nums ${
                validation.passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'
              }`}
            >
              {validation.score}
            </span>
          )}
        </div>

        {!validation ? (
          <p className="text-xs text-stone-400">开始输入后自动校验</p>
        ) : (
          <>
            <div
              className={`mb-3 rounded-lg px-3 py-2 text-xs font-medium ${
                validation.passed
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300'
              }`}
            >
              {validation.passed ? '✓ 校验通过' : '还有待修正项'}
            </div>

            {validation.compliance_issues.length > 0 && (
              <IssueGroup
                title="合规问题（必须处理）"
                tone="red"
                items={validation.compliance_issues}
              />
            )}
            {validation.spec_issues.length > 0 && (
              <IssueGroup title="规格问题" tone="amber" items={validation.spec_issues} />
            )}
            {validation.passed && (
              <p className="text-xs leading-relaxed text-stone-500">
                标题、正文、标签、埋词、AI 声明全部符合平台规格。
                下一环节是素材工坊（生成封面与配图）。
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/** 问题分组 */
function IssueGroup({
  title,
  items,
  tone,
}: {
  title: string
  items: string[]
  tone: 'red' | 'amber'
}) {
  const cls =
    tone === 'red'
      ? 'bg-red-50 text-red-700'
      : 'bg-amber-50 text-amber-700'
  return (
    <div className="mb-3">
      <div className={`mb-1.5 px-2 py-1 text-xs font-medium ${cls}`}>
        {title}（{items.length}）
      </div>
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="flex gap-1.5 text-xs leading-relaxed text-stone-600">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-stone-300" />
            {it}
          </li>
        ))}
      </ul>
    </div>
  )
}
