import type { ReactNode } from 'react'

/** 基础 UI 组件 —— 紫色渐变主题。
 *
 * 视觉规格来自设计令牌（见 index.css），改主题只需改 CSS 变量。
 */

/* ── 徽章配色 ── */
const TAG_TONES = {
  purple: 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300',
  green: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  amber: 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  sky: 'bg-sky-50 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300',
  rose: 'bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300',
  gray: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-700/40 dark:text-zinc-300',
} as const

export type TagTone = keyof typeof TAG_TONES

/** 渐变卡色调（四色轮换，呼应参考稿）*/
const GRAD_CLASSES: Record<string, string> = {
  purple: 'grad-purple',
  orange: 'grad-orange',
  coral: 'grad-coral',
  cyan: 'grad-cyan',
}

export function Tag({
  tone = 'gray',
  children,
}: {
  tone?: TagTone
  children: ReactNode
}) {
  return <span className={`tag ${TAG_TONES[tone]}`}>{children}</span>
}

/** 价值类型徽章 */
const VALUE_TONE: Record<string, TagTone> = {
  实用: 'green',
  信息: 'sky',
  情绪: 'rose',
  经济: 'amber',
}

export function ValueTypeBadge({ value }: { value: string }) {
  return <Tag tone={VALUE_TONE[value] ?? 'gray'}>{value || '未分类'}</Tag>
}

/** 差异化维度徽章 */
export function DiffBadge({ label }: { label: string }) {
  return <Tag tone="purple">{label}</Tag>
}

/** 状态徽章 */
const STATUS_MAP: Record<string, { text: string; tone: TagTone }> = {
  pooled: { text: '可执行', tone: 'gray' },
  claimed: { text: '建稿中', tone: 'sky' },
  done: { text: '已成稿', tone: 'green' },
  archived: { text: '已归档', tone: 'gray' },
}

export function StatusBadge({ status }: { status: string }) {
  const conf = STATUS_MAP[status] ?? { text: status, tone: 'gray' as TagTone }
  return <Tag tone={conf.tone}>{conf.text}</Tag>
}

/** 统计块：大数字 + 标签，参考稿风格 */
export function StatCard({
  label,
  value,
  hint,
  accent = false,
  index,
}: {
  label: string
  value: number | string
  hint?: string
  accent?: boolean
  /** 传入则渲染为渐变卡，按 index 轮换四色 */
  index?: number
}) {
  if (accent || index !== undefined) {
    // 四色轮换：紫 / 橙 / 橙红 / 青，对齐参考稿的功能卡
    const grads = ['purple', 'orange', 'coral', 'cyan']
    const grad = grads[(index ?? 0) % grads.length]
    return (
      <div className={`grad-card ${GRAD_CLASSES[grad]}`}>
        <div className="text-[11px] font-medium text-white/80">{label}</div>
        <div className="text-2xl font-semibold tabular-nums text-white">
          {value}
        </div>
        {hint && <div className="text-[11px] text-white/75">{hint}</div>}
      </div>
    )
  }

  return (
    <div className="card px-4 py-3.5">
      <div className="t-caption">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-stone-900 dark:text-stone-50">
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-stone-400">{hint}</div>}
    </div>
  )
}

/** 空状态 */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-stone-300 bg-white/50 py-16 text-center dark:border-zinc-700 dark:bg-zinc-900/30">
      <svg
        width="40"
        height="40"
        viewBox="0 0 48 48"
        fill="none"
        className="mb-3 opacity-40"
      >
        <rect
          x="8"
          y="10"
          width="32"
          height="28"
          rx="4"
          stroke="currentColor"
          strokeWidth="2"
          className="text-stone-400"
        />
        <path
          d="M14 20h20M14 26h12"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className="text-stone-300"
        />
      </svg>
      <div className="text-sm font-medium text-stone-600 dark:text-stone-300">
        {title}
      </div>
      {hint && <div className="mt-1 text-xs text-stone-400">{hint}</div>}
    </div>
  )
}

/** 在线状态点 */
export function StatusDot({
  online,
  label,
}: {
  online: boolean
  label: string
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          online ? 'bg-emerald-500' : 'bg-stone-300'
        }`}
        style={online ? { animation: 'pulse-dot 2.5s ease-in-out infinite' } : undefined}
      />
      <span className={online ? 'text-emerald-600' : 'text-stone-400'}>
        {label}
      </span>
    </span>
  )
}

/** 侧栏状态行（内核/桥接） */
export function SidebarStatus({
  ok,
  label,
}: {
  ok: boolean
  label: string
}) {
  return (
    <div className="flex items-center gap-2 px-1 py-1">
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          ok ? 'bg-emerald-400' : 'bg-white/30'
        }`}
      />
      <span className="truncate text-[11px] text-white/70">{label}</span>
    </div>
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
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="t-title">{title}</h2>
        {desc && <p className="mt-0.5 text-xs text-stone-500">{desc}</p>}
      </div>
      {action}
    </div>
  )
}

/** 小组件标题（卡片内） */
export function WidgetTitle({
  title,
  action,
}: {
  title: string
  action?: ReactNode
}) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <h3 className="t-section">{title}</h3>
      {action}
    </div>
  )
}
