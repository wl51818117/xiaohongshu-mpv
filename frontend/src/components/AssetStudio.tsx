import { useCallback, useState } from 'react'
import { type Draft, type Validation } from '../lib/api'
import { EmptyState, SectionTitle, Tag } from './ui'

type Style = 'realistic' | 'clean' | 'vivid'

const STYLES: { key: Style; label: string }[] = [
  { key: 'realistic', label: '写实摄影' },
  { key: 'clean', label: '干净极简' },
  { key: 'vivid', label: '色彩鲜明' },
]

/** 素材工坊：封面 / 内页 / 视频生产。
 *
 * 规格依据 docs/02：封面 3:4（1080×1440）出 3 候选，
 * 内页 4-8 张，视频必须图生视频（文生视频画面不可控）。
 */
export function AssetStudio({
  draft,
  onChanged,
  onNotify,
}: {
  draft: Draft | null
  onChanged: () => void
  onNotify: (kind: 'ok' | 'err', msg: string) => void
}) {
  const [style, setStyle] = useState<Style>('realistic')
  const [innerCount, setInnerCount] = useState(6)
  const [busy, setBusy] = useState<string | null>(null)
  const [coverCheck, setCoverCheck] = useState<Validation | null>(null)
  const [lastPrompt, setLastPrompt] = useState('')

  const cover = draft?.cover_url || ''
  const images = draft?.images || []

  const gen = useCallback(
    async (kind: 'cover' | 'inner') => {
      if (!draft) return
      setBusy(kind)
      try {
        const r = await fetch(
          `/api/assets/generate?draft_id=${draft.id}&kind=${kind}&style=${style}&count=${innerCount}`,
          { method: 'POST', headers: { 'content-type': 'application/json' } },
        )
        const data = await r.json()
        if (!data.ok) {
          onNotify('err', data.error || '生成失败')
          return
        }
        setLastPrompt(data.prompt || '')
        if (kind === 'cover') setCoverCheck(data.validation || null)
        onNotify(
          'ok',
          kind === 'cover'
            ? `已生成 ${data.files.length} 个封面候选`
            : `已生成 ${data.files.length} 张内页`,
        )
        onChanged()
      } catch (e) {
        onNotify('err', `生成失败：${(e as Error).message}`)
      } finally {
        setBusy(null)
      }
    },
    [draft, style, innerCount, onNotify, onChanged],
  )

  const compose = useCallback(async () => {
    if (!draft) return
    setBusy('video')
    try {
      const first = cover || images[0]
      if (!first) {
        onNotify('err', '请先生成封面（视频需要首帧图）')
        return
      }
      const vr = await fetch('/api/assets/video', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ draft_id: draft.id, first_frame: first, scene: 'push' }),
      })
      const vd = await vr.json()
      setLastPrompt(vd.prompt || '')
      onNotify('ok', `运镜方案：${vd.hint || '已生成'}`)
    } catch (e) {
      onNotify('err', `生成失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }, [draft, cover, images, onNotify])

  if (!draft) {
    return (
      <EmptyState
        title="请先在「稿件」页生成或选择一篇稿件"
        hint="素材工坊的所有产出都挂在具体稿件上"
      />
    )
  }

  return (
    <>
      <SectionTitle
        title="素材工坊"
        desc={`稿件 #${draft.id} ·${draft.title || '未命名'}`}
      />

      {/* 操作区 */}
      <div className="card mb-5 p-5">
        <div className="grid gap-4 md:grid-cols-3">
          {/* 封面 */}
          <div>
            <div className="field-label">封面风格</div>
            <div className="mb-2 flex gap-1.5">
              {STYLES.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setStyle(s.key)}
                  className={`btn btn-sm flex-1 ${
                    style === s.key ? 'btn-primary' : 'btn-ghost'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <button
              className="btn btn-primary w-full"
              disabled={busy !== null}
              onClick={() => void gen('cover')}
            >
              {busy === 'cover' ? '生成中…' : '生成封面（3 候选）'}
            </button>
            <p className="mt-1.5 text-[11px] text-stone-400">
              3:4 竖版 1080×1440 · 点击率 &lt;2.5% 难晋级流量池，出 3 个候选挑
            </p>
          </div>

          {/* 内页 */}
          <div>
            <div className="field-label">内页数量（4-8 张）</div>
            <select
              value={innerCount}
              onChange={(e) => setInnerCount(Number(e.target.value))}
              className="field mb-2"
            >
              {[4, 5, 6, 7, 8].map((n) => (
                <option key={n} value={n}>
                  {n} 张
                </option>
              ))}
            </select>
            <button
              className="btn btn-ghost w-full"
              disabled={busy !== null}
              onClick={() => void gen('inner')}
            >
              {busy === 'inner' ? '生成中…' : '生成内页'}
            </button>
            <p className="mt-1.5 text-[11px] text-stone-400">
              九宫格顺序：全景 → 细节 → 场景 → 对比 → 卖点
            </p>
          </div>

          {/* 视频 */}
          <div>
            <div className="field-label">视频链路</div>
            <button
              className="btn btn-ghost w-full"
              disabled={busy !== null || !cover}
              onClick={() => void compose()}
            >
              {busy === 'video' ? '生成中…' : '生成图生视频方案'}
            </button>
            <p className="mt-1.5 text-[11px] text-stone-400">
              ★ 必须图生视频（文生视频画面不可控）；单镜头 3-5 秒最稳
            </p>
          </div>
        </div>

        {/* 规格校验结果 */}
        {coverCheck && (
          <div
            className={`mt-4 rounded-lg px-3 py-2 text-xs ${
              coverCheck.passed
                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                : 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300'
            }`}
          >
            {coverCheck.passed
              ? '✓ 封面规格校验通过（3:4 / 1080×1440）'
              : `待修正：${(coverCheck.spec_issues || []).join('；')}`}
          </div>
        )}

        {lastPrompt && (
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-stone-500 hover:text-stone-700">
              查看生成提示词
            </summary>
            <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-stone-50 p-3 text-[11px] leading-relaxed text-stone-600 dark:bg-zinc-800 dark:text-zinc-300">
              {lastPrompt}
            </pre>
          </details>
        )}
      </div>

      {/* 封面候选 */}
      <div className="mb-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-50">
            封面候选
          </h3>
          <Tag tone="purple">3:4 · 1080×1440</Tag>
        </div>
        {images.filter((p) => p.includes('cover_')).length === 0 ? (
          <EmptyState title="还没有封面" hint="点上方「生成封面」出 3 个候选" />
        ) : (
          <div className="grid grid-cols-3 gap-3">
            {images
              .filter((p) => p.includes('cover_'))
              .map((p, i) => (
                <div key={p} className="card overflow-hidden">
                  <img
                    src={`/files/${p}`}
                    alt={`封面候选 ${i + 1}`}
                    className="aspect-[3/4] w-full object-cover"
                    loading="lazy"
                  />
                  <div className="px-2.5 py-1.5 text-center text-[11px] text-stone-500">
                    候选 {i + 1}
                    {p === cover && (
                      <span className="ml-1.5 text-purple-600">· 当前</span>
                    )}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* 内页 */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-50">
            内页（{images.filter((p) => p.includes('inner_')).length} 张）
          </h3>
          <span className="text-[11px] text-stone-400">
            AI 素材生命周期约 15 天，超期需换新
          </span>
        </div>
        {images.filter((p) => p.includes('inner_')).length === 0 ? (
          <EmptyState title="还没有内页" hint="点上方「生成内页」按九宫格顺序生成" />
        ) : (
          <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
            {images
              .filter((p) => p.includes('inner_'))
              .map((p, i) => (
                <div key={p} className="card overflow-hidden">
                  <img
                    src={`/files/${p}`}
                    alt={`内页 ${i + 1}`}
                    className="aspect-[3/4] w-full object-cover"
                    loading="lazy"
                  />
                  <div className="py-1 text-center text-[10px] text-stone-400">
                    {i + 1}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>
    </>
  )
}
