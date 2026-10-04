import { useCallback, useEffect, useState } from 'react'
import {
  notifyPanelsChanged,
  readPanels,
  type DynamicPanel,
} from '../bridge/extensibility'
import { Button, EmptyState, SectionTitle, Tag } from './ui'

/** Agent 动态面板的渲染器。
 *
 * ★ 这些面板不是我们写死在代码里的，而是 **Agent 运行时调用
 *   `ui_add_panel` 动态创建**的（持久化在 localStorage）。
 *   加功能不用改代码、不用重新构建。
 */
export function AgentPanels({
  current,
  onOpen,
}: {
  current: string | null
  onOpen: (id: string) => void
}) {
  const [panels, setPanels] = useState<DynamicPanel[]>([])
  const [dataCache, setDataCache] = useState<Record<string, unknown>>({})

  const load = useCallback(() => setPanels(readPanels()), [])

  useEffect(() => {
    load()
    // Agent 在同页面调用 ui_add_panel 后需要同步刷新
    const h = () => load()
    window.addEventListener('wb-panels-changed', h)
    const timer = setInterval(load, 4000) // 兜底轮询（Agent 侧可能同页写入）
    return () => {
      window.removeEventListener('wb-panels-changed', h)
      clearInterval(timer)
    }
  }, [load])

  // 拉取 source 型面板的数据
  useEffect(() => {
    let alive = true
    ;(async () => {
      const next: Record<string, unknown> = {}
      for (const p of panels) {
        if (p.source) {
          try {
            const r = await fetch(p.source)
            next[p.id] = r.ok ? await r.json() : { error: `HTTP ${r.status}` }
          } catch (e) {
            next[p.id] = { error: (e as Error).message }
          }
        } else if (p.data !== undefined) {
          next[p.id] = p.data
        }
      }
      if (alive) setDataCache(next)
    })()
    return () => {
      alive = false
    }
  }, [panels])

  const remove = (id: string) => {
    const raw = localStorage.getItem('wb-agent-capabilities')
    try {
      const p = raw ? JSON.parse(raw) : { panels: [], customSkills: [] }
      p.panels = (p.panels ?? []).filter((x: DynamicPanel) => x.id !== id)
      localStorage.setItem('wb-agent-capabilities', JSON.stringify(p))
      notifyPanelsChanged()
      load()
    } catch {
      /* 忽略损坏数据 */
    }
  }

  if (panels.length === 0) {
    return (
      <>
        <SectionTitle
          title="自定义面板"
          desc="Agent 在对话里说「加一个 XX 面板」就会出现在这里"
        />
        <EmptyState
          title="还没有自定义面板"
          hint="试试对 Agent 说：「加一个选题速览面板」或「加个数据对比面板」"
        />
      </>
    )
  }

  const shown = current ? panels.filter((p) => p.id === current) : panels

  return (
    <>
      <SectionTitle
        title="自定义面板"
        desc={`共 ${panels.length} 个 · 由 Agent 动态创建，改代码即可加功能`}
      />

      {current && panels.length > 1 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {panels.map((p) => (
            <Button
              key={p.id}
              variant={current === p.id ? 'primary' : 'default'}
              size="sm"
              onClick={() => onOpen(p.id)}
            >
              {p.title}
            </Button>
          ))}
        </div>
      )}

      <div className="space-y-3">
        {shown.map((p) => (
          <div key={p.id} className="card p-5">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-stone-900 dark:text-stone-50">
                  {p.title}
                </span>
                <Tag tone="purple">{p.kind}</Tag>
                <Tag tone="gray">{p.createdBy === 'agent' ? 'Agent 创建' : '手动'}</Tag>
              </div>
              <Button
                variant="danger"
                size="sm"
                onClick={() => remove(p.id)}
                aria-label={`删除 ${p.title}`}
              >
                删除
              </Button>
            </div>
            {p.note && (
              <p className="mb-3 text-xs text-stone-500">{p.note}</p>
            )}
            <PanelBody panel={p} data={dataCache[p.id]} />
          </div>
        ))}
      </div>
    </>
  )
}

/** 按面板类型渲染内容 */
function PanelBody({
  panel,
  data,
}: {
  panel: DynamicPanel
  data: unknown
}) {
  if (data === undefined) {
    return <div className="py-6 text-center text-xs text-stone-400">加载中…</div>
  }

  if (panel.kind === 'note') {
    return (
      <pre className="whitespace-pre-wrap text-sm leading-relaxed text-stone-700 dark:text-stone-200">
        {typeof data === 'string' ? data : JSON.stringify(data, null, 2)}
      </pre>
    )
  }

  if (panel.kind === 'metrics') {
    const obj = (data ?? {}) as Record<string, unknown>
    const entries = Object.entries(obj).filter(
      ([, v]) => typeof v === 'number' || typeof v === 'string',
    )
    if (!entries.length) {
      return (
        <pre className="text-xs text-stone-500">{JSON.stringify(data, null, 2)}</pre>
      )
    }
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {entries.map(([k, v]) => (
          <div key={k} className="rounded-xl bg-zinc-50 px-3 py-2.5 dark:bg-zinc-800/60">
            <div className="text-[11px] text-stone-400">{k}</div>
            <div className="mt-0.5 text-xl font-semibold tabular-nums text-stone-900 dark:text-stone-50">
              {String(v)}
            </div>
          </div>
        ))}
      </div>
    )
  }

  // list / detail：尽量提取数组渲染表格
  const arr = Array.isArray(data)
    ? data
    : Array.isArray((data as any)?.items)
      ? (data as any).items
      : Array.isArray((data as any)?.top_drafts)
        ? (data as any).top_drafts
        : null

  if (arr && arr.length > 0) {
    const cols = Object.keys(arr[0] as object).slice(0, 5)
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-stone-200 dark:border-zinc-700">
              {cols.map((c) => (
                <th key={c} className="py-2 pr-4 font-medium text-stone-500">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {arr.slice(0, 20).map((row: any, i: number) => (
              <tr
                key={i}
                className="border-b border-stone-100 dark:border-zinc-800"
              >
                {cols.map((c) => (
                  <td
                    key={c}
                    className="py-2 pr-4 text-stone-700 dark:text-stone-300"
                  >
                    {String(row?.[c] ?? '').slice(0, 60)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {arr.length > 20 && (
          <p className="mt-2 text-[11px] text-stone-400">
            共 {arr.length} 条，已显示前 20 条
          </p>
        )}
      </div>
    )
  }

  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-50 p-3 text-[11px] leading-relaxed text-stone-600 dark:bg-zinc-800 dark:text-stone-300">
      {JSON.stringify(data, null, 2).slice(0, 2000)}
    </pre>
  )
}
