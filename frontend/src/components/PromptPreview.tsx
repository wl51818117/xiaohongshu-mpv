import { useCallback, useEffect, useState } from 'react'
import { Button, Tag } from './ui'

/** 提示词预览与编辑面板。
 *
 * ★ 为什么要这个面板：
 *   1. 提示词现在由 prompt_engine 按 schema 生成，**结构化可逐块看**
 *   2. 生图前就能看到将要发出去的内容 —— 不必真花额度才发现写错
 *   3. 每个原子块可单独覆盖（光线换暖、构图换特写），改一处不动其他
 *   4. 预览不调生图 API，不消耗额度
 */

type Spec = {
  subject: string
  composition: string
  lighting: string
  material: string
  color: string
  typography: string
  negatives: string[]
  quality: string
  meta: Record<string, unknown>
  /** 允许按字段名索引（前端要动态遍历 FIELD_LABEL） */
  [key: string]: unknown
}

type PreviewItem = { index: number; spec: Spec; prompt: string }

const FIELD_LABEL: Record<string, string> = {
  subject: '画面主体',
  composition: '构图',
  lighting: '光影',
  material: '材质',
  color: '色彩',
  typography: '文字区',
  quality: '质量',
}

export function PromptPreview({
  draftId,
  kind,
  style,
  count,
  scene,
  onNotify,
}: {
  draftId: number | null
  kind: 'cover' | 'inner' | 'video'
  style: string
  count: number
  scene: string
  onNotify: (k: 'ok' | 'err', m: string) => void
}) {
  const [items, setItems] = useState<PreviewItem[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  /**用户覆盖：{ itemIndex: { field: value } } */
  const [override, setOverride] = useState<Record<number, Record<string, string>>>({})

  const load = useCallback(
    async (ov: Record<number, Record<string, string>>) => {
      if (!draftId) return
      setLoading(true)
      try {
        // 覆盖只对第一项生效（封面场景）；内页多张时逐张传
        const res = await Promise.all(
          ([1, 2, 3, 4, 5, 6, 7, 8] as const).map(async (i) => {
            const idx = kind === 'cover' || kind === 'video' ? 1 : i
            if (kind !== 'cover' && kind !== 'video' && idx > count) return null
            const r = await fetch('/api/assets/prompt/preview', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                draft_id: draftId,
                kind,
                style,
                count,
                scene,
                override: ov[idx] ?? {},
              }),
            })
            const d = await r.json()
            if (kind !== 'cover' && kind !== 'video') return null
            return (d.items?.[0] ?? null) as PreviewItem | null
          }),
        )
        if (kind === 'cover' || kind === 'video') {
          setItems(res.filter(Boolean) as PreviewItem[])
        } else {
          // 内页一次拿全部（后端已按 count 返回）
          const r = await fetch('/api/assets/prompt/preview', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ draft_id: draftId, kind, style, count }),
          })
          const d = await r.json()
          setItems((d.items ?? []) as PreviewItem[])
        }
      } catch (e) {
        onNotify('err', `提示词预览失败：${(e as Error).message}`)
      } finally {
        setLoading(false)
      }
    },
    [draftId, kind, style, count, scene, onNotify],
  )

  // 稿件/风格/数量变化时自动刷新
  useEffect(() => {
    if (open) void load(override)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, draftId, kind, style, count, scene])

  const edit = (itemIdx: number, field: string, value: string) => {
    setOverride((prev) => ({
      ...prev,
      [itemIdx]: { ...(prev[itemIdx] ?? {}), [field]: value },
    }))
  }

  const reset = () => {
    setOverride({})
    void load({})
  }

  if (!open) {
    return (
      <div className="mb-4">
        <Button variant="subtle" size="sm" onClick={() => setOpen(true)}>
          查看提示词（不消耗额度）
        </Button>
      </div>
    )
  }

  return (
    <div className="card mb-5 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-stone-800 dark:text-stone-100">
            提示词预览
          </div>
          <div className="mt-0.5 text-[11px] text-stone-400">
            按 schema 分块生成，可逐块覆盖。预览不调用生图 API。
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          {Object.keys(override).length > 0 && (
            <Button variant="ghost" size="xs" onClick={reset}>
              重置
            </Button>
          )}
          <Button variant="ghost" size="xs" loading={loading} onClick={() => void load(override)}>
            刷新
          </Button>
          <Button variant="ghost" size="xs" onClick={() => setOpen(false)}>
            收起
          </Button>
        </div>
      </div>

      {items.length === 0 && !loading && (
        <div className="py-6 text-center text-[12px] text-stone-400">
          {draftId ? '暂无提示词' : '请先选择稿件'}
        </div>
      )}

      <div className="space-y-3">
        {items.map((it) => {
          const ov = override[it.index] ?? {}
          const compLabel = (it.spec.meta?.comp_label as string) ?? ''
          const edited = Object.keys(ov).length > 0
          return (
            <div
              key={it.index}
              className="rounded-[var(--r-md)] border border-stone-200 p-3 dark:border-zinc-700"
            >
              <div className="mb-2.5 flex items-center gap-2">
                <Tag tone="purple">{it.index}</Tag>
                {compLabel && <Tag tone="sky">{compLabel}</Tag>}
                {edited && <Tag tone="amber">已改</Tag>}
              </div>

              <div className="space-y-2">
                {Object.entries(FIELD_LABEL).map(([field, label]) => {
                  const value = (ov[field] as string) ?? (it.spec[field] as string) ?? ''
                  if (!value && field !== 'subject') return null
                  const isEdited = ov[field] !== undefined
                  return (
                    <div key={field}>
                      <label className="mb-0.5 block text-[10px] font-medium text-stone-500">
                        {label}
                      </label>
                      <textarea
                        className="field resize-y !min-h-[34px] text-[11px] leading-relaxed"
                        style={
                          isEdited
                            ? { borderColor: 'var(--brand-400)' }
                            : { background: 'var(--surface-2)' }
                        }
                        value={value}
                        rows={field === 'subject' || field === 'composition' ? 2 : 1}
                        onChange={(e) => edit(it.index, field, e.target.value)}
                      />
                    </div>
                  )
                })}

                {it.spec.negatives?.length > 0 && (
                  <div>
                    <label className="mb-0.5 block text-[10px] font-medium text-stone-500">
                      负面约束（{it.spec.negatives.length}）
                    </label>
                    <div className="flex flex-wrap gap-1">
                      {it.spec.negatives.map((n) => (
                        <span
                          key={n}
                          className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-700 dark:bg-red-500/15 dark:text-red-300"
                        >
                          {n}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* 最终发给模型的完整文本 */}
              <details className="mt-2.5">
                <summary className="cursor-pointer text-[11px] text-stone-500">
                  查看最终提示词文本
                </summary>
                <pre className="mt-1.5 whitespace-pre-wrap break-words rounded bg-stone-50 p-2 text-[10px] leading-relaxed text-stone-600 dark:bg-zinc-800 dark:text-stone-300">
                  {it.prompt}
                </pre>
              </details>
            </div>
          )
        })}
      </div>

      {items.length > 0 && (
        <p className="mt-3 text-[11px] text-stone-400">
          改动只影响本次预览；正式点「生成」时会用当前结构化提示词。
        </p>
      )}
    </div>
  )
}
