import { useCallback, useEffect, useState } from 'react'
import { Button, EmptyState, SectionTitle, StatCard, Tag } from './ui'

/** 咨询台 + 商品库 —— 获客漏斗的中间段与锚点。
 *
 * ★ 咨询台为什么关键：
 *   私信率是**唯一真正的购买意向信号**——赞藏是「不错」，私信是「我要买」。
 *   现有指标全是流量与互动（曝光/点击/赞/藏/评/转/粉），
 *   漏斗中间这段整段不存在，等于无法判断「哪类内容值得继续做」。
 *
 * ★ 为什么刻意不录客户手机号/微信号：
 *   小红书 2026-09-18 新规后站外导流是重罚项（最高扣 2 万，关联账号同罪）。
 *   系统里存客户联系方式是给自己埋雷。私域靠站内店铺会员 + 群聊 + 私信。
 */

type Inquiry = {
  id: number
  raw_text: string
  intent: string
  stage: string
  channel: string
  amount: number
  note: string
  topic_id: number | null
  product_id: number | null
  created_at: string | null
}

type InquiryStats = {
  total: number
  by_stage: Record<string, number>
  by_intent: Record<string, number>
  ordered: number
  gmv: number
  close_rate: number
  top_words: { text: string; n: number }[]
}

type Product = {
  id: number
  name: string
  sku: string
  category: string
  price_band: string
  unit: string
  selling_points: string[]
  pain_points: string[]
  scenes: string[]
  certs: string[]
  moq: string
  notes: string
  topic_count: number
}

type Persona = {
  id: number
  name: string
  age_range: string
  life_stage: string
  concerns: string[]
  objections: string[]
  own_words: string[]
}

const STAGE_LABEL: Record<string, string> = {
  open: '待跟进',
  replied: '已回复',
  ordered: '已成交',
  lost: '未成交',
}

const STAGE_TONE: Record<string, 'gray' | 'sky' | 'green' | 'red'> = {
  open: 'amber' as unknown as 'gray',
  replied: 'sky',
  ordered: 'green',
  lost: 'red',
}

const CHANNEL_LABEL: Record<string, string> = {
  dm: '私信',
  comment: '评论',
  profile: '主页',
  group: '群聊',
}

export function CommerceDesk({ onNotify }: { onNotify: (k: 'ok' | 'err', m: string) => void }) {
  const [tab, setTab] = useState<'inquiries' | 'products' | 'personas'>('inquiries')
  const [inquiries, setInquiries] = useState<Inquiry[]>([])
  const [iStats, setIStats] = useState<InquiryStats | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [personas, setPersonas] = useState<Persona[]>([])

  // 录入咨询
  const [rawText, setRawText] = useState('')
  const [productId, setProductId] = useState<number | ''>('')
  const [busy, setBusy] = useState(false)

  // 新增商品
  const [pName, setPName] = useState('')
  const [pPrice, setPPrice] = useState('')
  const [pMoq, setPMoq] = useState('')
  const [pPoints, setPPoints] = useState('')
  const [pPains, setPPains] = useState('')
  const [pScenes, setPScenes] = useState('')
  const [pCerts, setPCerts] = useState('')

  const load = useCallback(async () => {
    try {
      const [inq, st, pr, ps] = await Promise.all([
        fetch('/api/inquiries?limit=60').then((r) => r.json()),
        fetch('/api/inquiries/stats').then((r) => r.json()),
        fetch('/api/products').then((r) => r.json()),
        fetch('/api/personas').then((r) => r.json()),
      ])
      setInquiries(inq.items || [])
      setIStats(st)
      setProducts(pr.items || [])
      setPersonas(ps.items || [])
    } catch (e) {
      onNotify('err', `读取失败：${(e as Error).message}`)
    }
  }, [onNotify])

  useEffect(() => {
    void load()
  }, [load])

  const addInquiry = async () => {
    if (!rawText.trim()) {
      onNotify('err', '请填用户原话')
      return
    }
    setBusy(true)
    try {
      const r = await fetch('/api/inquiries', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          raw_text: rawText.trim(),
          channel: 'dm',
          product_id: productId === '' ? null : Number(productId),
        }),
      })
      const d = await r.json()
      onNotify('ok', `已记录，识别意图：${d.intent || '未识别'}`)
      setRawText('')
      await load()
    } catch (e) {
      onNotify('err', `录入失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const setStage = async (id: number, stage: string, amount = 0) => {
    await fetch(`/api/inquiries/${id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ stage, amount }),
    })
    await load()
  }

  const addProduct = async () => {
    if (!pName.trim()) {
      onNotify('err', '请填商品名')
      return
    }
    setBusy(true)
    try {
      const r = await fetch('/api/products', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          name: pName.trim(),
          price_band: pPrice.trim(),
          moq: pMoq.trim(),
          selling_points: pPoints.split('\n').map((s) => s.trim()).filter(Boolean),
          pain_points: pPains.split('\n').map((s) => s.trim()).filter(Boolean),
          scenes: pScenes.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean),
          certs: pCerts.split('\n').map((s) => s.trim()).filter(Boolean),
        }),
      })
      const d = await r.json()
      if (!d.ok) {
        onNotify('err', d.detail || '创建失败')
        return
      }
      onNotify('ok', `已创建「${d.item.name}」`)
      setPName('')
      setPPrice('')
      setPMoq('')
      setPPoints('')
      setPPains('')
      setPScenes('')
      setPCerts('')
      await load()
    } catch (e) {
      onNotify('err', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const list = (arr: string[], cls = 'text-stone-600 dark:text-stone-300') =>
    arr.length === 0 ? (
      <span className="text-stone-300 dark:text-zinc-600">—</span>
    ) : (
      <span className={cls}>{arr.join('、')}</span>
    )

  return (
    <div>
      <SectionTitle
        title="获客台"
        desc="咨询记录（漏斗中间段）+ 商品与人群主数据。刻意不录客户联系方式——私域靠站内闭环。"
      />

      <div className="mb-4 flex gap-1 border-b border-[var(--border)]">
        {(
          [
            ['inquiries', '咨询记录'],
            ['products', '商品库'],
            ['personas', '人群卡'],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              tab === k
                ? 'border-orange-700 text-orange-700'
                : 'border-transparent text-stone-500 hover:text-stone-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── 咨询记录 ── */}
      {tab === 'inquiries' && (
        <div>
          {iStats && (
            <div className="mb-4 grid gap-3 sm:grid-cols-4">
              <StatCard label="咨询总数" value={iStats.total} hint="漏斗中间段" />
              <StatCard
                label="已成交"
                value={iStats.ordered}
                hint={`成交率 ${(iStats.close_rate * 100).toFixed(1)}%`}
              />
              <StatCard label="成交额" value={`¥${iStats.gmv}`} hint="GMV" />
              <StatCard
                label="待跟进"
                value={iStats.by_stage?.open ?? 0}
                hint={iStats.by_stage?.open ? '别忘了回私信' : '已清空'}
              />
            </div>
          )}

          <div className="card mb-4 p-4">
            <div className="mb-2 text-sm font-semibold text-stone-800 dark:text-stone-100">
              记一条咨询
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <textarea
                className="field flex-1 resize-y"
                rows={2}
                value={rawText}
                onChange={(e) => setRawText(e.target.value)}
                placeholder={'照抄用户私信里的原话。\n例：160斤穿XL勒不勒 / 能穿几次 / 有没有CMA报告'}
              />
              <div className="flex shrink-0 gap-2">
                <select
                  className="field w-32"
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
                <Button variant="primary" size="md" loading={busy} onClick={addInquiry}>
                  记录
                </Button>
              </div>
            </div>
            <p className="mt-1.5 text-[11px] text-stone-400">
              意图会按内衣/日用品行业词表自动识别（尺码/质量/发货/价格/资质/库存/对比/售后）
            </p>
          </div>

          {iStats?.top_words?.length ? (
            <div className="card mb-4 p-4">
              <div className="mb-2 text-[13px] font-medium text-stone-700 dark:text-stone-200">
                用户高频用词
                <span className="ml-1.5 text-[11px] font-normal text-stone-400">
                  这些是她们自己怎么说，比任何归纳都值钱
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {iStats.top_words.map((w) => (
                  <span
                    key={w.text}
                    className="rounded-full bg-[var(--surface-2)] px-2.5 py-1 text-[11px] text-stone-600 dark:text-stone-300"
                  >
                    {w.text}
                    <span className="ml-1 text-stone-400">{w.n}</span>
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {inquiries.length === 0 ? (
            <EmptyState
              title="还没有咨询记录"
              hint="私信是唯一真正的购买意向信号。记录 10 条就能看出用户最关心什么"
            />
          ) : (
            <div className="space-y-2">
              {inquiries.map((q) => (
                <div key={q.id} className="card p-3.5">
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Tag tone="purple">{CHANNEL_LABEL[q.channel] ?? q.channel}</Tag>
                      {q.intent && <Tag tone="sky">{q.intent}</Tag>}
                      <Tag tone={STAGE_TONE[q.stage] ?? 'gray'}>
                        {STAGE_LABEL[q.stage] ?? q.stage}
                      </Tag>
                      {q.created_at && (
                        <span className="text-[10px] text-stone-400">
                          {q.created_at.slice(0, 10)}
                        </span>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-1">
                      {q.stage === 'ordered' ? (
                        <>
                          <input
                            className="field !min-h-[26px] !w-20 !py-0.5 !text-[11px]"
                            placeholder="金额"
                            defaultValue={q.amount || ''}
                            onBlur={(e) =>
                              setStage(q.id, 'ordered', Number(e.target.value) || 0)
                            }
                          />
                        </>
                      ) : (
                        <>
                          <Button
                            variant="default"
                            size="xs"
                            onClick={() => setStage(q.id, 'replied')}
                          >
                            已回
                          </Button>
                          <Button
                            variant="primary"
                            size="xs"
                            onClick={() => setStage(q.id, 'ordered', q.amount || 0)}
                          >
                            成交
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="text-[13px] leading-relaxed text-stone-800 dark:text-stone-200">
                    「{q.raw_text}」
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 商品库 ── */}
      {tab === 'products' && (
        <div>
          <div className="card mb-4 p-4">
            <div className="mb-2 text-sm font-semibold text-stone-800 dark:text-stone-100">
              新增商品
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <input
                className="field sm:col-span-2"
                value={pName}
                onChange={(e) => setPName(e.target.value)}
                placeholder="商品名，例：一次性内裤（工厂直供）"
              />
              <input
                className="field"
                value={pPrice}
                onChange={(e) => setPPrice(e.target.value)}
                placeholder="价格带，例：9.9-19.9/条"
              />
              <input
                className="field"
                value={pMoq}
                onChange={(e) => setPMoq(e.target.value)}
                placeholder="起订量，例：100 条起订"
              />
              <div className="sm:col-span-2">
                <textarea
                  className="field resize-y !min-h-[52px] text-[12px]"
                  rows={2}
                  value={pPains}
                  onChange={(e) => setPPains(e.target.value)}
                  placeholder={'这货解决什么麻烦（一行一条）\n出差不想用酒店公用贴身用品\n经期担心闷'}
                />
              </div>
              <input
                className="field"
                value={pScenes}
                onChange={(e) => setPScenes(e.target.value)}
                placeholder="场景，逗号分隔：差旅,经期,通勤"
              />
              <div className="sm:col-span-2">
                <textarea
                  className="field resize-y !min-h-[52px] text-[12px]"
                  rows={2}
                  value={pPoints}
                  onChange={(e) => setPPoints(e.target.value)}
                  placeholder={'工厂硬卖点（一行一条）\n无纺布一体成型，无缝无标\n100条起订，7天打样'}
                />
              </div>
              <input
                className="field"
                value={pCerts}
                onChange={(e) => setPCerts(e.target.value)}
                placeholder={'检测报告（一行一条，可留空）\n★ 填了「抗菌」才能合法说'}
              />
            </div>
            <div className="mt-2 flex items-center gap-2">
              <Button variant="primary" size="md" loading={busy} onClick={addProduct}>
                创建
              </Button>
              <span className="text-[11px] text-stone-400">
                「检测报告」决定能不能说「抗菌/A类/母婴级」——没报告就是虚假背书
              </span>
            </div>
          </div>

          {products.length === 0 ? (
            <EmptyState title="商品库还是空的" hint="没有商品，选题就没有锚点" />
          ) : (
            <div className="space-y-2">
              {products.map((p) => (
                <div key={p.id} className="card p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-stone-800 dark:text-stone-100">
                        {p.name}
                      </span>
                      {p.price_band && <Tag tone="amber">{p.price_band}</Tag>}
                      {p.moq && <Tag tone="gray">{p.moq}</Tag>}
                    </div>
                    <span className="text-[11px] text-stone-400">
                      {p.topic_count} 条选题
                    </span>
                  </div>
                  <div className="grid gap-2 text-[12px] sm:grid-cols-2">
                    <div>
                      <span className="text-stone-400">解决麻烦：</span>
                      {list(p.pain_points)}
                    </div>
                    <div>
                      <span className="text-stone-400">工厂卖点：</span>
                      {list(p.selling_points)}
                    </div>
                    <div>
                      <span className="text-stone-400">场景：</span>
                      {list(p.scenes)}
                    </div>
                    <div>
                      <span className="text-stone-400">检测报告：</span>
                      {p.certs.length ? (
                        <span className="text-emerald-600 dark:text-emerald-400">
                          {p.certs.join('、')}
                        </span>
                      ) : (
                        <span className="text-amber-600 dark:text-amber-400">
                          无 — 不能说「抗菌/A类」
                        </span>
                      )}
                    </div>
                  </div>
                  {p.notes && (
                    <div className="mt-2 text-[11px] text-stone-500dark:text-stone-400">
                      备注：{p.notes}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 人群卡 ── */}
      {tab === 'personas' && (
        <div>
          <div className="mb-3 rounded-[var(--r-md)] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] leading-relaxed text-stone-500 dark:text-stone-400">
            这里的 <strong>concerns</strong>（痛点）应来自需求库的高置信度信号，
            而非拍脑袋填。用「需求采集」页的「回写进人群卡」自动同步。
            <strong>objections</strong>（反对意见）单独列出——内容一多半该在回答它们，而不是赞美商品。
          </div>
          {personas.length === 0 ? (
            <EmptyState title="还没有人群卡" hint="先在需求采集页录入需求，再回写" />
          ) : (
            <div className="space-y-2">
              {personas.map((p) => (
                <div key={p.id} className="card p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-sm font-medium text-stone-800 dark:text-stone-100">
                      {p.name}
                    </span>
                    {p.age_range && <Tag tone="gray">{p.age_range}</Tag>}
                    {p.life_stage && <Tag tone="sky">{p.life_stage}</Tag>}
                  </div>
                  <div className="grid gap-2 text-[12px] sm:grid-cols-2">
                    <div>
                      <span className="text-stone-400">痛点：</span>
                      {list(p.concerns)}
                    </div>
                    <div>
                      <span className="text-stone-400">反对意见：</span>
                      {list(p.objections)}
                    </div>
                  </div>
                  {p.own_words.length > 0 && (
                    <div className="mt-2 border-t border-stone-200 pt-2 dark:border-zinc-700">
                      <div className="mb-1 text-[11px] text-stone-400">
                        她们自己怎么说（最值钱）
                      </div>
                      <div className="space-y-0.5">
                        {p.own_words.slice(0, 5).map((w, i) => (
                          <div key={i} className="text-[12px] text-stone-600 dark:text-stone-300">
                            「{w}」
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
