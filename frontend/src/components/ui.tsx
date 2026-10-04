import type { ReactNode } from 'react'

/** 价值类型徽章配色 */
const VALUE_STYLE: Record<string, string> = {
  实用: 'bg-emerald-50 text-emerald-700',
  信息: 'bg-sky-50 text-sky-700',
  情绪: 'bg-rose-50 text-rose-700',
  经济: 'bg-amber-50 text-amber-700',
}

export function ValueTypeBadge({ value }: { value: string }) {
  return (
    <span className={`tag ${VALUE_STYLE[value] ?? 'bg-stone-100 text-stone-600'}`}>
      {value || '未分类'}
    </span>
  )
}

/** 差异化维度徽章 */
export function DiffBadge({ label }: { label: string }) {
  return (
    <span className="tag bg-orange-50 text-orange-700 border border-orange-100">
      {label}
    </span>
  )
}

/** 状态徽章 */
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { text: string; cls: string }> = {
    pooled: { text: '可执行', cls: 'bg-stone-100 text-stone-600' },
    claimed: { text: '建稿中', cls: 'bg-sky-50 text-sky-700' },
    done: { text: '已成稿', cls: 'bg-emerald-50 text-emerald-700' },
    archived: { text: '已归档', cls: 'bg-stone-100 text-stone-400' },
  }
  const conf = map[status] ?? { text: status, cls: 'bg-stone-100 text-stone-600' }
  return <span className={`tag ${conf.cls}`}>{conf.text}</span>
}

/** 统计数字块 */
export function StatCard({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: number | string
  hint?: string
  tone?: 'default' | 'accent'
}) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-stone-500">{label}</div>
      <div
        className={`mt-1 text-2xl font-semibold tabular-nums ${
          tone === 'accent' ? 'text-orange-700' : 'text-stone-900'
        }`}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-stone-400">{hint}</div>}
    </div>
  )
}

/** 空状态 */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-stone-300 py-14 text-center">
      <div className="text-sm font-medium text-stone-600">{title}</div>
      {hint && <div className="mt-1 text-xs text-stone-400">{hint}</div>}
    </div>
  )
}

/** 内核在线指示 */
export function KernelDot({ online }: { online: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          online ? 'bg-emerald-500' : 'bg-stone-300'
        }`}
      />
      <span className={online ? 'text-emerald-700' : 'text-stone-400'}>
        内核{online ? '在线' : '离线'}
      </span>
    </span>
  )
}

/** 分区标题 */
export function SectionTitle({
  title,
  desc,
  action,
}: {
  title: string
  desc?: string
  action?: ReactNode
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-base font-semibold text-stone-900">{title}</h2>
        {desc && <p className="mt-0.5 text-xs text-stone-500">{desc}</p>}
      </div>
      {action}
    </div>
  )
}
