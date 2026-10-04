import { useCallback, useEffect, useState } from 'react'
import { Button, Tag } from './ui'

type Feed = { name: string; url: string; category: string }
type Group = { key: string; label: string; desc: string; feeds: Feed[] }

/** RSS 源管理面板（设置 → RSS 源）。
 *
 * 目的：不必手改 backend/data/feeds.json 就能增删改赛道与源，
 * 并可**逐个测试源是否还活着** —— RSS 源失效是常态（实测多个源已挂）。
 */
export function FeedsManager({
  onNotify,
}: {
  onNotify: (kind: 'ok' | 'err', msg: string) => void
}) {
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState<string>('')
  const [probeResult, setProbeResult] = useState<Record<string, string>>({})
  const [adding, setAdding] = useState<Record<string, Feed>>({})

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/feeds').then((x) => x.json())
      setGroups(r.presets ?? [])
    } catch (e) {
      onNotify('err', `读取失败：${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [onNotify])

  useEffect(() => {
    void load()
  }, [load])

  const persist = async (next: Group[], msg: string) => {
    setSaving(true)
    try {
      const r = await fetch('/api/feeds', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ presets: next }),
      }).then((x) => x.json())
      if (r.ok) {
        onNotify('ok', `${msg}（共 ${r.group_count} 赛道 / ${r.feed_count} 源）`)
        await load()
      } else {
        onNotify('err', '保存失败')
      }
    } catch (e) {
      onNotify('err', `保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const addFeed = async (g: Group) => {
    const draft = (adding[g.key] ?? { name: '', url: '', category: '综合' }) as Feed
    if (!draft.name.trim() || !draft.url.trim()) {
      onNotify('err', '源名称和地址都要填')
      return
    }
    const next = groups.map((x) =>
      x.key === g.key
        ? {
            ...x,
            feeds: [
              ...x.feeds,
              {
                name: draft.name.trim(),
                url: draft.url.trim(),
                category: draft.category.trim() || '综合',
              },
            ],
          }
        : x,
    )
    setAdding((a) => ({ ...a, [g.key]: { name: '', url: '', category: '综合' } }))
    await persist(next, `已添加源「${draft.name.trim()}」`)
  }

  const removeFeed = (g: Group, url: string) => {
    const name = g.feeds.find((f) => f.url === url)?.name ?? url
    const next = groups.map((x) =>
      x.key === g.key ? { ...x, feeds: x.feeds.filter((f) => f.url !== url) } : x,
    )
    void persist(next, `已删除源「${name}」`)
  }

  const removeGroup = (g: Group) => {
    void persist(groups.filter((x) => x.key !== g.key), `已删除赛道「${g.label}」`)
  }

  const probe = async (f: Feed) => {
    setProbing(f.url)
    setProbeResult((r) => ({ ...r, [f.url]: '检测中…' }))
    try {
      const r = await fetch('/api/feeds/probe', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: f.url }),
      }).then((x) => x.json())
      const text = r.ok ? `✓ ${r.count} 条` : `✗ ${r.reason ?? '不可用'}`
      setProbeResult((p) => ({ ...p, [f.url]: text }))
    } catch (e) {
      setProbeResult((p) => ({ ...p, [f.url]: `✗ ${(e as Error).message.slice(0, 30)}` }))
    } finally {
      setProbing('')
    }
  }

  const probeAll = async () => {
    const all = groups.flatMap((g) => g.feeds)
    for (const f of all) {
      await probe(f)
    }
  }

  if (loading) {
    return <div className="py-10 text-center text-sm text-stone-400">加载中…</div>
  }

  const totalFeeds = groups.reduce((a, g) => a + g.feeds.length, 0)

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-stone-900 dark:text-stone-50">
            RSS 源
          </div>
          <div className="mt-0.5 text-[11px] text-stone-400">
            共 {groups.length} 个赛道 · {totalFeeds} 个源
          </div>
        </div>
        <button
          className="btn-ghost btn-sm"
          disabled={saving}
          onClick={() => void probeAll()}
        >
          全部检测
        </button>
      </div>

      <p className="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
        RSS 源会失效（实测多个源已挂）。建议定期点「全部检测」，
        <strong>把失效源删掉</strong> —— 采集时失效源会被跳过，
        但留着会误导判断。配置保存在
        <code className="mx-1">backend/data/feeds.json</code>。
      </p>

      <div className="space-y-4">
        {groups.map((g) => {
          const draft = adding[g.key] ?? { name: '', url: '', category: '综合' }
          return (
            <div key={g.key} className="rounded-xl border border-stone-200 p-3.5 dark:border-zinc-700">
              <div className="mb-2.5 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-stone-800 dark:text-stone-100">
                    {g.label}
                  </span>
                  <Tag tone="purple">{g.feeds.length} 源</Tag>
                  {g.desc && (
                    <span className="text-[11px] text-stone-400">{g.desc}</span>
                  )}
                </div>
                <Button
                  variant="danger"
                  size="sm"
                  disabled={saving}
                  onClick={() => removeGroup(g)}
                  aria-label={`删除赛道 ${g.label}`}
                >
                  删赛道
                </Button>
              </div>

              {/* 源列表 */}
              <div className="mb-2.5 space-y-1">
                {g.feeds.map((f) => (
                  <div
                    key={f.url}
                    className="flex items-center gap-2 rounded-lg bg-stone-50 px-2.5 py-1.5 dark:bg-zinc-800/60"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs text-stone-700 dark:text-stone-200">
                        {f.name}
                      </span>
                      <span className="block truncate font-mono text-[10px] text-stone-400">
                        {f.url}
                      </span>
                    </span>
                    <Tag tone="gray">{f.category}</Tag>
                    {probeResult[f.url] && (
                      <span
                        className={`shrink-0 text-[10px] ${
                          probeResult[f.url].startsWith('✓')
                            ? 'text-emerald-600'
                            : probeResult[f.url] === '检测中…'
                              ? 'text-stone-400'
                              : 'text-red-600'
                        }`}
                      >
                        {probeResult[f.url]}
                      </span>
                    )}
                    <button
                      className="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-stone-500 hover:bg-stone-200 dark:hover:bg-zinc-700"
                      disabled={probing === f.url}
                      onClick={() => void probe(f)}
                    >
                      {probing === f.url ? '···' : '检测'}
                    </button>
                    <button
                      className="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10"
                      disabled={saving}
                      onClick={() => removeFeed(g, f.url)}
                      aria-label={`删除源 ${f.name}`}
                    >
                      删
                    </button>
                  </div>
                ))}
              </div>

              {/* 新增源 */}
              <div className="flex flex-wrap gap-1.5">
                <input
                  value={draft.name}
                  onChange={(e) =>
                    setAdding((a) => ({ ...a, [g.key]: { ...draft, name: e.target.value } }))
                  }
                  placeholder="源名称"
                  className="field !min-h-[30px] !w-24 !py-1 !text-xs"
                />
                <input
                  value={draft.url}
                  onChange={(e) =>
                    setAdding((a) => ({ ...a, [g.key]: { ...draft, url: e.target.value } }))
                  }
                  placeholder="https://example.com/rss"
                  className="field !min-h-[30px] !flex-1 !py-1 !font-mono !text-[11px]"
                />
                <input
                  value={draft.category}
                  onChange={(e) =>
                    setAdding((a) => ({
                      ...a,
                      [g.key]: { ...draft, category: e.target.value },
                    }))
                  }
                  placeholder="分类"
                  className="field !min-h-[30px] !w-16 !py-1 !text-xs"
                />
                <Button
                  variant="primary"
                  size="sm"
                  disabled={saving}
                  onClick={() => void addFeed(g)}
                >
                  + 加源
                </Button>
              </div>
            </div>
          )
        })}
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-stone-400">
        想加新赛道？目前可在
        <code>backend/data/feeds.json</code> 加一个 key，
        或让我（Agent）帮你改。
      </p>
    </div>
  )
}
