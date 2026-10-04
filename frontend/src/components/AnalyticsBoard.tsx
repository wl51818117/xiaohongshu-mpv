import { useCallback, useEffect, useState } from 'react'
import { Button, EmptyState, SectionTitle, Tag } from './ui'

type Derived = {
  interaction_rate: number
  collect_rate: number
  follow_rate: number
  advice: { action: string; reason: string }
}

type Dashboard = {
  materials: number
  topics_total: number
  topics_pooled: number
  drafts_total: number
  drafts_with_metrics: number
  drafts_passed: number
  note: string
}

type Pattern = {
  sampled: number
  top_drafts: {
    draft_id: number
    title: string
    score: number
    keyword: string
    persona: string
    value_type: string
    length: number
  }[]
  value_type_dist: Record<string, number>
  persona_dist: Record<string, number>
  avg_length: number
  note: string
}

const ADVICE_STYLE: Record<string, { label: string; tone: 'green' | 'amber' | 'gray' | 'sky' }> = {
  spend: { label: '建议投放', tone: 'green' },
  fix_cover: { label: '先改封面', tone: 'amber' },
  add_value: { label: '先补价值', tone: 'amber' },
  hold: { label: '暂不投放', tone: 'gray' },
}

/** 数据看板：录入表现 → 投流建议 → 爆文要素 → 回流选题。
 *
 * ★ 数据需手动录入或后续接官方 API —— 本系统不代抓小红书数据。
 */
export function AnalyticsBoard({
  draftId,
  onNotify,
  onChanged,
}: {
  draftId: number | null
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onChanged: () => void
}) {
  const [dash, setDash] = useState<Dashboard | null>(null)
  const [derived, setDerived] = useState<Derived | null>(null)
  const [patterns, setPatterns] = useState<Pattern | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [form, setForm] = useState({
    impressions: '',
    reads: '',
    likes: '',
    collects: '',
    comments: '',
    shares: '',
    follows: '',
    ctr: '',
    read_rate: '',
  })

  const load = useCallback(async () => {
    try {
      const [d, p] = await Promise.all([
        fetch('/api/analytics/dashboard').then((r) => r.json()),
        fetch('/api/analytics/patterns?limit=8').then((r) => r.json()),
      ])
      setDash(d)
      setPatterns(p)
      if (draftId) {
        const h = await fetch(`/api/analytics/metrics/${draftId}`).then((r) => r.json())
        setDerived(h.derived ?? null)
      }
    } catch {
      /* 静默：看板是辅助信息，失败不打扰用户 */
    }
  }, [draftId])

  useEffect(() => {
    void load()
  }, [load])

  const submit = async () => {
    if (!draftId) {
      onNotify('err', '请先在「稿件」页生成一篇稿件')
      return
    }
    setBusy('submit')
    try {
      const num = (k: string) => Number(form[k as keyof typeof form] || 0)
      // 百分比输入转小数
      const pct = (k: string) => Number(form[k as keyof typeof form] || 0) / 100
      const resp = await fetch('/api/analytics/metrics', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          draft_id: draftId,
          impressions: num('impressions'),
          reads: num('reads'),
          likes: num('likes'),
          collects: num('collects'),
          comments: num('comments'),
          shares: num('shares'),
          follows: num('follows'),
          ctr: pct('ctr'),
          read_rate: pct('read_rate'),
        }),
      })
      const d = await resp.json()
      setDerived(d.derived)
      onNotify('ok', `已记录：${d.advice.reason}`)
      void load()
      onChanged()
    } catch (e) {
      onNotify('err', `录入失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const feedTopics = async () => {
    setBusy('feed')
    try {
      const r = await fetch('/api/analytics/patterns/feed?limit=8', { method: 'POST' })
      const d = await r.json()
      onNotify(
        d.created > 0 ? 'ok' : 'err',
        d.created > 0
          ? `已回流 ${d.created} 条新选题到选题库`
          : '暂无新选题（可能已存在或样本不足）',
      )
      onChanged()
    } catch (e) {
      onNotify('err', `回流失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const fields: { k: keyof typeof form; label: string; suffix?: string }[] = [
    { k: 'impressions', label: '曝光' },
    { k: 'reads', label: '阅读' },
    { k: 'likes', label: '点赞' },
    { k: 'collects', label: '收藏' },
    { k: 'comments', label: '评论' },
    { k: 'shares', label: '分享' },
    { k: 'follows', label: '涨粉' },
    { k: 'ctr', label: '点击率', suffix: '%' },
    { k: 'read_rate', label: '完读率', suffix: '%' },
  ]

  return (
    <>
      <SectionTitle
        title="数据看板"
        desc="录入表现 → 投流建议 → 提炼爆文要素 → 回流选题"
      />

      {/* 总览 */}
      {dash && (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            { label: '素材', value: dash.materials },
            { label: '选题', value: dash.topics_total },
            { label: '可执行选题', value: dash.topics_pooled },
            { label: '稿件', value: dash.drafts_total },
            { label: '已录数据', value: dash.drafts_with_metrics },
          ].map((s, i) => (
            <div
              key={s.label}
              className={`card px-4 py-3 ${i === 4 ? 'border-orange-200' : ''}`}
            >
              <div className="t-caption">{s.label}</div>
              <div className="mt-1 text-xl font-semibold tabular-nums text-stone-900 dark:text-stone-50">
                {s.value}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* 录入 */}
        <div className="card p-5">
          <h3 className="t-section mb-1">录入表现数据</h3>
          <p className="mb-4 text-[11px] text-stone-400">
            建议在发布后第 1 / 3 / 7 / 14 天各录一次
            {!draftId && ' · 当前未选稿件'}
          </p>
          <div className="grid grid-cols-3 gap-2.5">
            {fields.map((f) => (
              <div key={f.k}>
                <label className="field-label !mb-1 !text-[11px]">
                  {f.label}
                  {f.suffix ? `(${f.suffix})` : ''}
                </label>
                <input
                  type="number"
                  value={form[f.k]}
                  onChange={(e) => setForm({ ...form, [f.k]: e.target.value })}
                  className="field !min-h-[34px] !py-1.5 !text-xs"
                  placeholder="0"
                />
              </div>
            ))}
          </div>
          <Button
            variant="primary"
            size="md"
            className="mt-4 w-full"
            loading={busy === 'submit'}
            disabled={busy !== null}
            onClick={() => void submit()}
          >
            记录并生成投流建议
          </Button>
          <p className="mt-2 text-[11px] leading-relaxed text-stone-400">
            {dash?.note ?? '数据需手动录入或接官方 API —— 本系统不代抓小红书数据'}
          </p>
        </div>

        {/* 投流建议 */}
        <div className="card p-5">
          <h3 className="t-section mb-3">投流建议</h3>
          {!derived ? (
            <EmptyState title="还没有数据" hint="左侧录入表现数据后自动生成" />
          ) : (
            <>
              <div
                className={`mb-3 rounded-lg px-3 py-2.5 text-sm ${
                  ADVICE_STYLE[derived.advice.action]?.tone === 'green'
                    ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                    : ADVICE_STYLE[derived.advice.action]?.tone === 'amber'
                      ? 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300'
                      : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                }`}
              >
                <div className="font-medium">
                  {ADVICE_STYLE[derived.advice.action]?.label ?? derived.advice.action}
                </div>
                <div className="mt-0.5 text-xs">{derived.advice.reason}</div>
              </div>

              <div className="space-y-2">
                {[
                  { l: '互动率', v: derived.interaction_rate },
                  { l: '收藏率', v: derived.collect_rate },
                  { l: '涨粉率', v: derived.follow_rate },
                ].map((r) => (
                  <div key={r.l} className="flex items-center gap-3">
                    <span className="w-14 text-xs text-stone-500">{r.l}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-[var(--grad-purple-from)] to-[var(--grad-purple-to)]"
                        style={{ width: `${Math.min(r.v * 500, 100)}%` }}
                      />
                    </div>
                    <span className="w-12 text-right text-xs tabular-nums text-stone-600">
                      {(r.v * 100).toFixed(2)}%
                    </span>
                  </div>
                ))}
              </div>

              <div className="mt-4 rounded-lg bg-zinc-50 p-2.5 text-[11px] leading-relaxed text-stone-500 dark:bg-zinc-800/60">
                判定阈值：薯条 需点击≥5% 且完读≥40%；聚光 需 48h赞藏≥100 且点击≥3%。
                <strong>收藏率 ≥3% 是前置门槛</strong>——收藏低说明内容没价值，投了也白费。
              </div>
            </>
          )}
        </div>
      </div>

      {/* 爆文要素 */}
      <div className="card mt-4 p-5">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="t-section">爆文要素提炼</h3>
            <p className="mt-0.5 text-[11px] text-stone-400">
              已采样 {patterns?.sampled ?? 0} 篇有数据的稿件
            </p>
          </div>
          <Button
            variant="primary"
            size="sm"
            loading={busy === 'feed'}
            disabled={busy !== null || (patterns?.sampled ?? 0) === 0}
            onClick={() => void feedTopics()}
          >
            回流为新选题
          </Button>
        </div>

        {!patterns || patterns.top_drafts.length === 0 ? (
          <EmptyState
            title="还没有可提炼的数据"
            hint="先录入表现数据，系统会自动归纳标题结构、选题类型与人群分布"
          />
        ) : (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                {
                  l: '价值类型分布',
                  v: Object.entries(patterns.value_type_dist || {})
                    .map(([k, n]) => `${k}×${n}`)
                    .join('  ') || '—',
                },
                {
                  l: '人群分布',
                  v:
                    Object.entries(patterns.persona_dist || {})
                      .map(([k, n]) => `${k}×${n}`)
                      .join('  ') || '—',
                },
                { l: '平均正文长度', v: `${patterns.avg_length} 字` },
                { l: '高分组稿件', v: `${patterns.top_drafts.length} 篇` },
              ].map((x) => (
                <div key={x.l} className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/60">
                  <div className="text-[11px] text-stone-400">{x.l}</div>
                  <div className="mt-1 text-xs font-medium text-stone-700 dark:text-stone-200">
                    {x.v}
                  </div>
                </div>
              ))}
            </div>

            <div className="space-y-1.5">
              {patterns.top_drafts.slice(0, 6).map((d) => (
                <div
                  key={d.draft_id}
                  className="flex items-center gap-3 rounded-lg border border-stone-100 px-3 py-2 dark:border-zinc-800"
                >
                  <span className="min-w-0 flex-1 truncate text-sm text-stone-700 dark:text-stone-200">
                    {d.title}
                  </span>
                  <Tag tone="purple">{d.value_type || '未分类'}</Tag>
                  <span className="text-[11px] tabular-nums text-stone-400">
                    高分 {d.score.toFixed(3)}
                  </span>
                </div>
              ))}
            </div>

            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
              {patterns.note}
            </p>
          </>
        )}
      </div>
    </>
  )
}
