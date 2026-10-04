import { useCallback, useEffect, useState } from 'react'
import { Button, EmptyState, SectionTitle, StatCard, Tag } from './ui'

/** 知识库页：踩坑与复盘经验的检索、沉淀与回顾。
 *
 * 数据来源两类：
 *  1. Obsidian 库导入（`E:/ob知识库/codex/30-知识库`）—— 四段式结构化笔记
 *  2. 运行时记录的错误本（MistakeLog）—— 支持标记已复盘
 *
 * 关键设计：同一份数据也注册成了 Agent 工具（search_knowledge 等），
 * 所以界面上看到的和 Agent 检索到的是同一份知识。
 */

type Item = {
  id: number
  title: string
  kind: string
  why: string
  how: string
  pitfall: string
  tags: string
  hit_count: number
  score?: number
}

type Mistake = {
  id: number
  scene: string
  symptom: string
  cause: string
  fix: string
  reviewed: boolean
}

type Stats = {
  total: number
  by_kind: Record<string, number>
  mistakes: number
  pending_review: number
  top_hit: { id: number; title: string; hits: number }[]
}

/** 持续增强概览 */
type Growth = {
  total: number
  active: number
  retired: number
  strong: number
  weak: number
  total_hits: number
  pending_review: number
  top: { title: string; score: number; hits: number }[]
  weakest: { title: string; score: number; hits: number }[]
}

const KIND_TONE: Record<string, 'red' | 'amber' | 'sky' | 'purple'> = {
  pitfall: 'red',
  spec: 'sky',
  insight: 'purple',
  review: 'amber',
}

const KIND_LABEL: Record<string, string> = {
  pitfall: '踩坑',
  spec: '规格',
  insight: '经验',
  review: '复盘',
}

export function KnowledgeBase({ onNotify }: { onNotify: (k: 'ok' | 'err', m: string) => void }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [growth, setGrowth] = useState<Growth | null>(null)
  const [items, setItems] = useState<Item[]>([])
  const [mistakes, setMistakes] = useState<Mistake[]>([])
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [busy, setBusy] = useState(false)

  const loadGrowth = useCallback(async () => {
    try {
      const r = await fetch('/api/knowledge/growth')
      setGrowth(await r.json())
    } catch {
      /* 静默 */
    }
  }, [])

  /** 投票：认可 +1 / 质疑 -1，分数归零自动退休 */
  const vote = async (title: string, action: 'agree' | 'challenge') => {
    try {
      const r = await fetch('/api/knowledge/vote', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ title, action }),
      })
      const d = await r.json()
      if (d.ok) {
        onNotify(
          'ok',
          action === 'agree'
            ? `已认可「${title.slice(0, 20)}」→ ${d.score} 分`
            : `已质疑「${title.slice(0, 20)}」→ ${d.status === 'retired' ? '已退休' : `${d.score} 分`}`
        )
        await Promise.all([loadGrowth(), loadItems()])
      }
    } catch (e) {
      onNotify('err', (e as Error).message)
    }
  }

  const loadStats = useCallback(async () => {
    try {
      const r = await fetch('/api/knowledge/stats')
      setStats(await r.json())
    } catch {
      /* 静默 */
    }
  }, [])

  const loadItems = useCallback(async () => {
    const url = q
      ? `/api/knowledge/search?q=${encodeURIComponent(q)}&limit=20${kind ? `&kind=${kind}` : ''}`
      : `/api/knowledge/items?limit=20${kind ? `&kind=${kind}` : ''}`
    // 检索接口是 POST+body，浏览是 GET —— 这里统一处理
    try {
      const r = q
        ? await fetch('/api/knowledge/search', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ query: q, limit: 20, kind }),
          })
        : await fetch(url)
      const d = await r.json()
      setItems(d.items || [])
    } catch (e) {
      onNotify('err', `读取知识库失败：${(e as Error).message}`)
    }
  }, [q, kind, onNotify])

  const loadMistakes = useCallback(async () => {
    try {
      const r = await fetch('/api/knowledge/mistakes?limit=8')
      const d = await r.json()
      setMistakes(d.items || [])
    } catch {
      /* 静默 */
    }
  }, [])

  useEffect(() => {
    void loadStats()
    void loadItems()
    void loadMistakes()
    void loadGrowth()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  const search = () => {
    setBusy(true)
    void loadItems().finally(() => setBusy(false))
  }

  const importObs = async () => {
    setBusy(true)
    try {
      const r = await fetch('/api/knowledge/import/obsidian', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ root: 'E:/ob知识库/codex', subdir: '30-知识库' }),
      })
      const d = await r.json()
      if (!d.ok) {
        onNotify('err', d.error || '导入失败')
        return
      }
      onNotify('ok', `导入完成：新增 ${d.created}、更新 ${d.updated}，库内共 ${d.total} 条`)
      await loadStats()
      await loadItems()
    } catch (e) {
      onNotify('err', `导入失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const markReviewed = async (id: number) => {
    try {
      await fetch(`/api/knowledge/mistakes/${id}/review`, { method: 'POST' })
      await loadMistakes()
      await loadStats()
      await loadGrowth()
    } catch (e) {
      onNotify('err', `标记失败：${(e as Error).message}`)
    }
  }

  /** 复盘结论晋升为正式经验 —— 这是「复盘」真正产生价值的一步 */
  const promote = async (id: number) => {
    try {
      const r = await fetch(`/api/knowledge/mistakes/${id}/promote`, { method: 'POST' })
      const d = await r.json()
      if (!d.ok) {
        onNotify('err', d.error || '晋升失败')
        return
      }
      onNotify('ok', '已晋升为正式经验，之后 Agent 生成时会借鉴它')
      await Promise.all([loadMistakes(), loadStats(), loadGrowth(), loadItems()])
    } catch (e) {
      onNotify('err', `晋升失败：${(e as Error).message}`)
    }
  }

  return (
    <div>
      <SectionTitle
        title="知识库"
        desc="历史踩坑与复盘经验。同一份数据也注册成了 Agent 工具，Agent 动手前会先查这里。"
      />

      {/* 概览 */}
      {stats && (
        <div className="mb-4 grid gap-3 sm:grid-cols-4">
          <StatCard label="知识条目" value={stats.total} hint="沉淀的经验" />
          <StatCard label="错误记录" value={stats.mistakes} hint="运行时踩坑" />
          <StatCard
            label="待复盘"
            value={stats.pending_review}
            hint={stats.pending_review > 0 ? '建议本周回顾' : '已全部复盘'}
          />
          <StatCard
            label="最常命中"
            value={stats.top_hit?.[0]?.hits ?? 0}
            hint={stats.top_hit?.[0]?.title?.slice(0, 12) ?? '暂无'}
          />
        </div>
      )}

      {/* 持续增强面板：让「复盘 → 晋升 → 投票」在界面上可见 */}
      {growth && (
        <div className="card mb-4 p-4">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-stone-800 dark:text-stone-100">
                持续增强
              </div>
              <div className="mt-0.5 text-[11px] text-stone-400">
                经验被验证越多分数越高，长期没人用/被质疑会自动退休（借鉴 ExpeL 投票制）
              </div>
            </div>
            <div className="flex gap-1.5">
              <Button
                variant="default"
                size="xs"
                loading={busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    const r = await fetch('/api/knowledge/import/obsidian', {
                      method: 'POST',
                      headers: { 'content-type': 'application/json' },
                      body: JSON.stringify({ root: 'E:/ob知识库/codex', subdir: '' }),
                    })
                    const d = await r.json()
                    if (d.ok) {
                      onNotify(
                        'ok',
                        `已同步 Obsidian 全库：扫描 ${d.files_scanned} 个文件，新增 ${d.created} 条`
                      )
                      await Promise.all([loadStats(), loadItems(), loadGrowth()])
                    }
                  } catch (e) {
                    onNotify('err', (e as Error).message)
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                同步 OB 全库
              </Button>
              <Button
                variant="ghost"
                size="xs"
                loading={busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    const r = await fetch('/api/knowledge/audit', { method: 'POST' })
                    const d = await r.json()
                    onNotify(
                      d.skipped ? 'ok' : 'ok',
                      d.skipped
                        ? `暂不审查：${d.reason}`
                        : `审查完成，退休 ${d.retired} 条低分经验`
                    )
                    await loadGrowth()
                  } catch (e) {
                    onNotify('err', (e as Error).message)
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                审查低分
              </Button>
            </div>
          </div>

          <div className="mb-3 grid gap-2 sm:grid-cols-4">
            <div className="rounded-[var(--r-sm)] bg-[var(--surface-2)] px-3 py-2">
              <div className="text-[10px] text-stone-400">活跃经验</div>
              <div className="text-base font-semibold tabular-nums text-stone-700 dark:text-stone-200">
                {growth.active}
              </div>
            </div>
            <div className="rounded-[var(--r-sm)] bg-[var(--surface-2)] px-3 py-2">
              <div className="text-[10px] text-stone-400">已退休</div>
              <div className="text-base font-semibold tabular-nums text-stone-500 dark:text-stone-400">
                {growth.retired}
              </div>
            </div>
            <div className="rounded-[var(--r-sm)] bg-[var(--surface-2)] px-3 py-2">
              <div className="text-[10px] text-stone-400">注入模型</div>
              <div className="text-base font-semibold tabular-nums text-brand-600 dark:text-brand-300">
                {growth.total_hits}
                <span className="ml-1 text-[10px] font-normal">次</span>
              </div>
            </div>
            <div className="rounded-[var(--r-sm)] bg-[var(--surface-2)] px-3 py-2">
              <div className="text-[10px] text-stone-400">待复盘</div>
              <div
                className={`text-base font-semibold tabular-nums ${
                  growth.pending_review > 0
                    ? 'text-amber-600 dark:text-amber-400'
                    : 'text-stone-500'
                }`}
              >
                {growth.pending_review}
              </div>
            </div>
          </div>

          {growth.top?.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-medium text-stone-500">
                投票分最高的经验（检索时优先注入）
              </div>
              <div className="space-y-1">
                {growth.top.slice(0, 4).map((t) => (
                  <div
                    key={t.title}
                    className="flex items-center gap-2 rounded-[var(--r-sm)] px-2 py-1 text-[11px] hover:bg-[var(--surface-2)]"
                  >
                    <span className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 font-medium tabular-nums text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
                      {t.score}分
                    </span>
                    <span className="min-w-0 flex-1 truncate text-stone-600 dark:text-stone-300">
                      {t.title}
                    </span>
                    <span className="shrink-0 text-[10px] text-stone-400">
                      命中 {t.hits}
                    </span>
                    <button
                      onClick={() => void vote(t.title, 'agree')}
                      className="shrink-0 text-[10px] text-brand-600 hover:underline dark:text-brand-300"
                      title="我认可这条经验（+1 分）"
                    >
                      认可
                    </button>
                    <button
                      onClick={() => void vote(t.title, 'challenge')}
                      className="shrink-0 text-[10px] text-stone-400 hover:text-red-600 hover:underline"
                      title="这条不适用（-1 分）"
                    >
                      质疑
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 检索栏 */}
      <div className="card mb-4 p-4">
        <div className="flex flex-wrap gap-2">
          <input
            className="field flex-1"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="检索经验，如「标题 字数」「埋词」「CSS 覆盖」"
          />
          <select className="field w-32" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">全部类型</option>
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <Button variant="primary" size="md" loading={busy} onClick={search}>
            检索
          </Button>
          <Button variant="default" size="md" disabled={busy} onClick={importObs}>
            从 Obsidian 导入
          </Button>
        </div>
        <p className="mt-2 text-[11px] text-stone-400">
          关键词检索（中文 2-gram + 标签加权），标题命中权重最高。命中的条目会自动累计次数，用于后续挑高频复看。
        </p>
      </div>

      {/* 错误本：定期回顾 */}
      {mistakes.length > 0 && (
        <div className="card mb-4 p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-semibold text-stone-800 dark:text-stone-100">
              错误本 · 待回顾
            </div>
            <span className="text-[11px] text-stone-400">最久未复盘的排在前面</span>
          </div>
          <div className="space-y-2">
            {mistakes.map((m) => (
              <div
                key={m.id}
                className="flex items-start justify-between gap-3 rounded-[var(--r-md)] border border-stone-200 px-3 py-2 dark:border-zinc-700"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    {m.scene && <Tag tone="gray">{m.scene}</Tag>}
                    <span className="text-[13px] text-stone-800 dark:text-stone-100">
                      {m.symptom}
                    </span>
                  </div>
                  {m.cause && (
                    <div className="mt-1 text-[12px] text-stone-500 dark:text-stone-400">
                      根因：{m.cause}
                    </div>
                  )}
                  {m.fix && (
                    <div className="mt-0.5 text-[12px] text-emerald-600 dark:text-emerald-400">
                      修复：{m.fix}
                    </div>
                  )}
                </div>
                {m.reviewed ? (
                  <Tag tone="green">已复盘</Tag>
                ) : (
                  <div className="flex shrink-0 flex-col gap-1">
                    <Button
                      variant="primary"
                      size="xs"
                      onClick={() => void promote(m.id)}
                      title="复盘结论沉淀成正式经验，之后会被 Agent 检索到"
                    >
                      晋升为经验
                    </Button>
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => markReviewed(m.id)}
                    >
                      标记已复盘
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 条目列表 */}
      <div className="space-y-2.5">
        {items.length === 0 ? (
          <EmptyState
            title="知识库还是空的"
            hint="点上方「从 Obsidian 导入」把已有笔记灌进来，或让 Agent 在工作中自动沉淀。"
          />
        ) : (
          items.map((it) => (
            <div key={it.id} className="card p-4">
              <div className="mb-1.5 flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Tag tone={KIND_TONE[it.kind] ?? 'gray'}>{KIND_LABEL[it.kind] ?? it.kind}</Tag>
                  <span className="text-[14px] font-medium text-stone-800 dark:text-stone-100">
                    {it.title}
                  </span>
                </div>
                <div className="shrink-0 text-[11px] text-stone-400">
                  {it.score !== undefined && <span className="mr-2">{it.score} 分</span>}
                  命中 {it.hit_count} 次
                </div>
              </div>
              {it.why && (
                <div className="mt-1.5 text-[12px] leading-relaxed text-stone-600 dark:text-stone-300">
                  <span className="font-medium text-stone-500">为什么：</span>
                  {it.why}
                </div>
              )}
              {it.how && (
                <div className="mt-1 text-[12px] leading-relaxed text-stone-600 dark:text-stone-300">
                  <span className="font-medium text-stone-500">怎么用：</span>
                  {it.how}
                </div>
              )}
              {it.pitfall && (
                <div className="mt-1 text-[12px] leading-relaxed text-amber-700 dark:text-amber-400">
                  <span className="font-medium">反例：</span>
                  {it.pitfall}
                </div>
              )}
              {it.tags && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {it.tags
                    .split(',')
                    .filter(Boolean)
                    .map((t) => (
                      <span key={t} className="text-[10px] text-stone-400">
                        #{t}
                      </span>
                    ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
