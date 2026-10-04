import { useState } from 'react'
import { FeedsManager } from './FeedsManager'
import { ApiProfiles } from './ApiProfiles'

/** 设置抽屉：双标签页 —— API 配置 / RSS 源。
 *
 * 需求对应：
 *  1. 配置表单（名称/BaseURL/Key/模型）+ 多套切换 + 加密存储 → ApiProfiles
 *  2. 连接测试（四态区分 + 耗时）→ ApiProfiles
 *  3. 模型清单拉取 + 手动兜底 → ApiProfiles
 *  4. 模型下拉（搜索/联动/生效态）→ ApiProfiles
 *  5. 加载态/防重复提交 → ApiProfiles
 * RSS 源管理 → FeedsManager
 */
export function SettingsPanel({
  open,
  onClose,
  onNotify,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onSaved: () => void
}) {
  const [tab, setTab] = useState<'api' | 'feeds'>('api')

  if (!open) return null

  return (
    <>
      {/* 遮罩 */}
      <div
        className="fixed inset-0 z-40 bg-stone-900/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* 抽屉 */}
      <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg animate-in flex-col border-l border-[var(--border)] bg-white shadow-2xl dark:bg-zinc-900">
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div>
            <div className="text-sm font-semibold text-stone-900 dark:text-stone-50">
              设置
            </div>
            <div className="mt-0.5 text-[11px] text-stone-400">
              API 配置与数据源
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="关闭设置"
            className="rounded-lg p-1.5 text-stone-400 transition-colors hover:bg-stone-100 dark:hover:bg-zinc-800"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* 标签页 */}
        <div className="flex shrink-0 gap-1 border-b border-[var(--border)] px-5">
          {(
            [
              ['api', 'API 配置'],
              ['feeds', 'RSS 源'],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${
                tab === k
                  ? 'border-orange-700 text-orange-700 dark:border-orange-400 dark:text-orange-400'
                  : 'border-transparent text-stone-500 hover:text-stone-700'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 内容 */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {tab === 'feeds' ? (
            <FeedsManager onNotify={onNotify} />
          ) : (
            <ApiProfiles onNotify={onNotify} onChanged={onSaved} />
          )}
        </div>

        {/* 底部 */}
        <div className="flex shrink-0 gap-2 border-t border-[var(--border)] px-5 py-3">
          <button className="btn btn-ghost flex-1" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </>
  )
}
