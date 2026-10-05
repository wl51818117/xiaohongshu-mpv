import { useCallback, useEffect, useState } from 'react'
import { Button, EmptyState, SectionTitle, StatCard, Tag } from './ui'

/** 需求采集台 —— 把「用户原话」变成可验证的需求数据。
 *
 * ★ 为什么这个页面最重要：
 *   需求库是整个系统的**输入端**。它空了，选题引擎就会明确拒绝工作
 *   （这是刻意的：宁可返回空，也不让 AI 拿拍脑袋的痛点去编选题）。
 *   所以这个页面的目标很单纯：**让人工录入足够快，快到能坚持。**
 *
 *   设计取舍：
 *   - 必填只有「原话」一个字段，其他都能默认
 *   - 来源有快捷按钮（按渠道分组），不用下拉里翻
 *   - 痛点标签自动归类，人工可改
 *   - 同一痛点重复录入会**自动累加证据数**，不需要手动操作
 */

type Signal = {
  id: number
  verbatim: string
  topic: string
  signal_type: string
  persona: string
  scene: string
  source: string
  source_ref: string
  brand: string
  intensity: number
  evidence_count: number
  verified: boolean
  note: string
  created_at: string | null
}

type Stats = {
  total: number
  verified: number
  high_confidence: number
  by_source: Record<string, number>
  by_type: Record<string, number>
  top_topics: { topic: string; n: number }[]
  distinct_sources: number
  single_source_risk: boolean
  engine_ready: boolean
  hint: string
}

type Persona = { id: number; name: string }
type Product = { id: number; name: string }

/** 采集渠道分组：按「证据强度」排，最有价值的放最前 */
const SOURCE_GROUPS: { label: string; hint: string; items: { key: string; label: string }[] }[] = [
  {
    label: '★ 你独占的（优先）',
    hint: '信息密度最高，零合规风险',
    items: [
      { key: 'alibaba_inquiry', label: '1688 询盘' },
      { key: 'own_support', label: '自己客服/售后' },
    ],
  },
  {
    label: '竞品与用户',
    hint: '注意只收中评/差评/问大家',
    items: [
      { key: 'ecommerce_review', label: '电商中评差评' },
      { key: 'xiaohongshu_comment', label: '小红书评论区' },
      { key: 'competitor_comment', label: '竞品评论区' },
    ],
  },
  {
    label: '辅助',
    hint: '信号较弱，谨慎使用',
    items: [
      { key: 'xiaohongshu_search', label: '搜索下拉词' },
      { key: 'manual', label: '口述/其他' },
    ],
  },
]

const SOURCE_LABEL: Record<string, string> = Object.fromEntries(
  SOURCE_GROUPS.flatMap((g) => g.items.map((i) => [i.key, i.label])),
)

const TYPE_LABEL: Record<string, string> = {
  symptom: '症状',
  param: '参数化追问',
  objection: '反对意见',
  scene: '场景',
}

const INTENSITY = [
  { v: 1, label: '弱' },
  { v: 2, label: '中' },
  { v: 3, label: '强' },
]

export function DemandCollector({ onNotify }: { onNotify: (k: 'ok' | 'err', m: string) => void }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [items, setItems] = useState<Signal[]>([])
  const [personas, setPersonas] = useState<Persona[]>([])
  const [products, setProducts] = useState<Product[]>([])

  // ── 录入表单（只强制要原话）──
  const [verbatim, setVerbatim] = useState('')
  const [source, setSource] = useState('alibaba_inquiry')
  const [sourceRef, setSourceRef] = useState('')
  const [persona, setPersona] = useState('')
  const [scene, setScene] = useState('')
  const [productId, setProductId] = useState<number | ''>('')
  const [brand, setBrand] = useState('')
  const [intensity, setIntensity] = useState(1)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, list, ps, pr] = await Promise.all([
        fetch('/api/demands/stats').then((r) => r.json()),
        fetch('/api/demands?limit=60').then((r) => r.json()),
        fetch('/api/personas').then((r) => r.json()),
        fetch('/api/products').then((r) => r.json()),
      ])
      setStats(s)
      setItems(list.items || [])
      setPersonas(ps.items || [])
      setProducts(pr.items || [])
    } catch (e) {
      onNotify('err', `读取需求库失败：${(e as Error).message}`)
    }
  }, [onNotify])

  useEffect(() => {
    void load()
  }, [load])

  const submit = async () => {
    if (!verbatim.trim()) {
      onNotify('err', '请填用户原话（唯一必填项）')
      return
    }
    setBusy(true)
    try {
      const r = await fetch('/api/demands', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          verbatim: verbatim.trim(),
          source,
          source_ref: sourceRef.trim(),
          persona,
          scene,
          product_id: productId === '' ? null : Number(productId),
          brand: brand.trim(),
          intensity,
          note: note.trim(),
        }),
      })
      const d = await r.json()
      if (d.action === 'merged') {
        onNotify(
          'ok',
          `已合并到「${d.topic}」，证据数 ${d.evidence_count}` +
            (d.evidence_count >= 3 ? ' ★ 达高置信度' : '')
        )
      } else {
        onNotify('ok', `已录入，归类为「${d.topic || '未归类'}」`)
      }
      // 清空关键字段，保留来源与人群（连续录入同渠道时更快）
      setVerbatim('')
      setSourceRef('')
      setNote('')
      await load()
    } catch (e) {
      onNotify('err', `录入失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const verify = async (id: number) => {
    await fetch(`/api/demands/${id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ verified: true }),
    })
    await load()
  }

  const remove = async (id: number) => {
    await fetch(`/api/demands/${id}`, { method: 'DELETE' })
    await load()
  }

  const syncPersonas = async () => {
    const r = await fetch('/api/demands/sync-personas', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    })
    const d = await r.json()
    if (d.ok) {
      onNotify('ok', `已把 ${d.topics_synced} 条高置信度需求回写进人群卡`)
      await load()
    }
  }

  return (
    <div>
      <SectionTitle
        title="需求采集"
        desc="记录用户原话与证据。同一痛点被多个渠道提到会自动累加，≥3 次才算高置信度，选题引擎只信高置信度的。"
      />

      {/* ── 引擎就绪状态 ── */}
      {stats && !stats.engine_ready && (
        <div className="mb-4 rounded-[var(--r-md)] border border-amber-200 bg-amber-50 px-4 py-3 text-[12px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          <strong className="font-medium">选题引擎当前处于「拒绝工作」状态</strong>
          <div className="mt-1">
            {stats.hint}
          </div>
          <div className="mt-1.5 text-[11px] opacity-80">
            为什么不自动兜底：没有真实需求时生成选题，本质还是 AI 拍脑袋——
            这正是之前选题跑偏（充电桩、清关、BERT 词嵌入）的根因。
          </div>
        </div>
      )}
      {stats?.single_source_risk && (
        <div className="mb-4 rounded-[var(--r-md)] border border-orange-200 bg-orange-50 px-4 py-2.5 text-[12px] text-orange-800 dark:border-orange-500/30 dark:bg-orange-500/10 dark:text-orange-300">
          ⚠️ 目前只靠单一渠道采集。容易把「渠道偏好」误当「用户需求」，
          建议至少补到 3 个渠道。
        </div>
      )}

      {/* ── 概览 ── */}
      {stats && (
        <div className="mb-5 grid gap-3 sm:grid-cols-4">
          <StatCard label="需求信号" value={stats.total} hint="已录入" />
          <StatCard
            label="高置信度"
            value={stats.high_confidence}
            hint="跨来源 ≥3 次"
            accent={stats.high_confidence >= 5}
          />
          <StatCard label="来源数" value={stats.distinct_sources} hint="渠道多样性" />
          <StatCard
            label="引擎状态"
            value={stats.engine_ready ? '已就绪' : '待数据'}
            hint={stats.engine_ready ? '可生成选题' : '需 ≥5 条高置信度'}
          />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        {/* ── 录入表单 ── */}
        <div className="card h-fit p-5">
          <div className="mb-3 text-sm font-semibold text-stone-800 dark:text-stone-100">
            录入一条需求
          </div>

          <div className="space-y-3">
            <div>
              <label className="field-label">
                用户原话 <span className="text-red-500">*</span>
              </label>
              <textarea
                className="field resize-y"
                rows={3}
                value={verbatim}
                onChange={(e) => setVerbatim(e.target.value)}
                placeholder={'照抄用户说的，不要改写。\n例：走路会掉裆 / 160斤能穿吗 / 穿了会痒'}
              />
              <p className="mt-1 text-[11px] text-stone-400">
                「走路会掉裆」比「产品存在质量问题」有用得多——症状才能指向解法
              </p>
            </div>

            <div>
              <label className="field-label">来源渠道</label>
              <div className="space-y-2">
                {SOURCE_GROUPS.map((g) => (
                  <div key={g.label}>
                    <div className="mb-1 text-[10px] text-stone-400">
                      {g.label}
                      <span className="ml-1 opacity-70">{g.hint}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {g.items.map((it) => (
                        <button
                          key={it.key}
                          onClick={() => setSource(it.key)}
                          className={`rounded-full px-2.5 py-1 text-[11px] transition-colors ${
                            source === it.key
                              ? 'bg-brand-500 text-white'
                              : 'bg-[var(--surface-2)] text-stone-500 hover:bg-stone-100 dark:hover:bg-zinc-800'
                          }`}
                        >
                          {it.label}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <label className="field-label">可复核引用（建议填）</label>
              <input
                className="field"
                value={sourceRef}
                onChange={(e) => setSourceRef(e.target.value)}
                placeholder="笔记标题+日期 / 差评原文片段 / 询盘编号"
              />
              <p className="mt-1 text-[11px] text-stone-400">
                填了才能被标记为「已确认」；不填也能录入，只是不能进选题引擎
              </p>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="field-label">人群</label>
                <select
                  className="field"
                  value={persona}
                  onChange={(e) => setPersona(e.target.value)}
                >
                  <option value="">不限</option>
                  {personas.map((p) => (
                    <option key={p.id} value={p.name}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field-label">场景</label>
                <input
                  className="field"
                  value={scene}
                  onChange={(e) => setScene(e.target.value)}
                  placeholder="差旅/经期/通勤"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="field-label">品牌（竞品差评时填）</label>
                <input
                  className="field"
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                />
              </div>
              <div>
                <label className="field-label">情绪强度</label>
                <select
                  className="field"
                  value={intensity}
                  onChange={(e) => setIntensity(Number(e.target.value))}
                >
                  {INTENSITY.map((i) => (
                    <option key={i.v} value={i.v}>
                      {i.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className="field-label">关联商品（可选）</label>
              <select
                className="field"
                value={productId}
                onChange={(e) => setProductId(e.target.value === '' ? '' : Number(e.target.value))}
              >
                <option value="">不关联</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            <Button
              variant="primary"
              size="md"
              className="w-full"
              loading={busy}
              onClick={submit}
            >
              录入
            </Button>

            {stats && stats.high_confidence > 0 && (
              <Button
                variant="default"
                size="sm"
                className="w-full"
                onClick={syncPersonas}
              >
                把高置信度需求回写进人群卡
              </Button>
            )}
          </div>
        </div>

        {/* ── 列表 ── */}
        <div>
          {items.length === 0 ? (
            <EmptyState
              title="还没有需求记录"
              hint="从最快的渠道开始：1688 询盘（你独占的信息源）或竞品商品页的中评/差评"
            />
          ) : (
            <div className="space-y-2">
              {items.map((s) => (
                <div key={s.id} className="card p-3.5">
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {s.topic && s.topic !== '未归类' ? (
                        <Tag tone={s.evidence_count >= 3 ? 'green' : 'gray'}>{s.topic}</Tag>
                      ) : (
                        <Tag tone="gray">未归类</Tag>
                      )}
                      <Tag tone="sky">{TYPE_LABEL[s.signal_type] ?? s.signal_type}</Tag>
                      <span className="text-[10px] text-stone-400">
                        {SOURCE_LABEL[s.source] ?? s.source}
                      </span>
                      {s.brand && <span className="text-[10px] text-stone-400">{s.brand}</span>}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums ${
                          s.evidence_count >= 3
                            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                            : 'bg-stone-100 text-stone-500 dark:bg-zinc-700 dark:text-stone-300'
                        }`}
                        title="跨来源印证次数，≥3 算高置信度"
                      >
                        证据 {s.evidence_count}
                      </span>
                      {s.verified ? (
                        <Tag tone="green">已确认</Tag>
                      ) : (
                        <Button variant="ghost" size="xs" onClick={() => verify(s.id)}>
                          确认
                        </Button>
                      )}
                      <Button variant="ghost" size="xs" onClick={() => remove(s.id)}>
                        删
                      </Button>
                    </div>
                  </div>

                  <div className="text-[13px] leading-relaxed text-stone-800 dark:text-stone-200">
                    「{s.verbatim}」
                  </div>

                  {s.source_ref && (
                    <div className="mt-1.5 text-[11px] text-stone-400">
                      引用：{s.source_ref}
                    </div>
                  )}
                  {s.note && s.note.includes('[+') && (
                    <div className="mt-1.5 whitespace-pre-wrap text-[11px] text-stone-500 dark:text-stone-400">
                      {s.note}
                    </div>
                  )}

                  <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-stone-400">
                    {s.persona && <span>人群：{s.persona}</span>}
                    {s.scene && <span>场景：{s.scene}</span>}
                    <span>情绪：{INTENSITY.find((i) => i.v === s.intensity)?.label}</span>
                    {s.created_at && <span>{s.created_at.slice(0, 10)}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
