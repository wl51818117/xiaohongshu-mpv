import { useMemo, useState } from 'react'
import { STEPS, type StepDef, type StepStatus } from '../lib/flow'

/** 内容生产思维导图。
 *
 * ★ 为什么不用 X6 流程图：
 *   流程图是「线性步骤条」，看不出**分支与汇聚**——
 *   实际生产里图文与视频在「素材」之后分岔、在「发布」之前汇聚，
 *   还有「数据回流 → 选题」的反向环。思维导图的树状结构能表达这些。
 *
 * 设计：
 *   根节点 = 内容生产
 *   一级分支 = 4 个阶段（采集/ 创作 / 产出 / 复盘）
 *   二级 = 各阶段的具体步骤（对应页签，点击可跳）
 *   状态由后端真实数据计算，不用写死
 */

type Branch = {
  id: string
  title: string
  icon: string
  color: string
  steps: StepDef[]
  /** 该分支适用的链路 */
  chains: ('图文' | '视频')[]
}

const BRANCHES: Branch[] = [
  {
    id: 'collect',
    title: '采集与选题',
    icon: '◈',
    color: 'var(--grad-orange-from)',
    chains: ['图文', '视频'],
    steps: STEPS.filter((s) => ['collect', 'convert'].includes(s.id)),
  },
  {
    id: 'create',
    title: '文案创作',
    icon: '✎',
    color: 'var(--grad-purple-from)',
    chains: ['图文', '视频'],
    steps: STEPS.filter((s) => ['copy'].includes(s.id)),
  },
  {
    id: 'produce',
    title: '素材产出',
    icon: '◐',
    color: 'var(--grad-coral-from)',
    chains: ['图文', '视频'],
    steps: STEPS.filter((s) => ['cover', 'inner', 'video', 'compose'].includes(s.id)),
  },
  {
    id: 'review',
    title: '发布与复盘',
    icon: '◉',
    color: 'var(--grad-cyan-from)',
    chains: ['图文', '视频'],
    steps: STEPS.filter((s) => ['publish', 'analytics'].includes(s.id)),
  },
]

const STATUS_STYLE: Record<StepStatus, { dot: string; label: string }> = {
  done: { dot: 'bg-emerald-500', label: '已完成' },
  active: { dot: 'bg-amber-500', label: '进行中' },
  pending: { dot: 'bg-stone-300 dark:bg-zinc-600', label: '待开始' },
  blocked: { dot: 'bg-red-500', label: '受阻' },
}

export function MindMap({
  statusOf,
  onGo,
  pipelineType,
}: {
  /** 由后端真实数据计算步骤状态 */
  statusOf: (id: string) => StepStatus
  onGo: (route: StepDef['route']) => void
  pipelineType: 'image' | 'video'
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({
    collect: true,
    create: true,
    produce: true,
    review: false,
  })
  const [hover, setHover] = useState<string | null>(null)

  const toggle = (id: string) => setOpen((p) => ({ ...p, [id]: !p[id] }))

  // 只显示当前链路包含的步骤
  const visible = useMemo(
    () =>
      BRANCHES.map((b) => ({
        ...b,
        steps: b.steps.filter((s) =>
          pipelineType === 'video' ? s.video : s.image,
        ),
      })).filter((b) => b.steps.length > 0),
    [pipelineType],
  )

  const doneCount = visible.reduce(
    (n, b) => n + b.steps.filter((s) => statusOf(s.id) === 'done').length,
    0,
  )
  const totalCount = visible.reduce((n, b) => n + b.steps.length, 0)

  return (
    <div className="card p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="t-section flex items-center gap-2">
            内容生产思维导图
            <span className="text-[11px] font-normal text-stone-400">
              {pipelineType === 'video' ? '视频链路' : '图文链路'}
            </span>
          </div>
          <div className="mt-0.5 text-[11px] text-stone-400">
            进度 {doneCount}/{totalCount} · 状态取自真实数据，点击节点进入对应页
          </div>
        </div>
        <div className="flex gap-1.5">
          {(['collect', 'create', 'produce', 'review'] as const).map((id) => {
            const b = BRANCHES.find((x) => x.id === id)!
            return (
              <button
                key={id}
                onClick={() => toggle(id)}
                className={`rounded-full px-2.5 py-1 text-[11px] transition-colors ${
                  open[id]
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300'
                    : 'text-stone-400 hover:bg-stone-100 dark:hover:bg-zinc-800'
                }`}
              >
                {b.title}
              </button>
            )
          })}
        </div>
      </div>

      {/* 根节点 */}
      <div className="mb-4 flex items-center gap-3">
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-white"
          style={{ background: 'linear-gradient(135deg, var(--grad-purple-from), var(--grad-purple-to))' }}
        >
          <span className="text-base">✦</span>
        </div>
        <div>
          <div className="text-sm font-semibold text-stone-800 dark:text-stone-100">
            小红书内容生产
          </div>
          <div className="text-[11px] text-stone-400">
            {doneCount === totalCount
              ? '全链路已跑通'
              : `还有 ${totalCount - doneCount} 步待完成`}
          </div>
        </div>
      </div>

      {/* 分支树 */}
      <div className="space-y-2">
        {visible.map((b) => (
          <div key={b.id} className="relative pl-5">
            {/* 竖线 */}
            <span className="absolute left-[9px] top-0 h-full w-px bg-[var(--border)]" />
            {/* 节点圆点 */}
            <span
              className="absolute left-[5px] top-4 h-2 w-2 rounded-full ring-2 ring-[var(--surface)]"
              style={{ background: b.color }}
            />

            <div className="rounded-[var(--r-md)] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <button
                  onClick={() => toggle(b.id)}
                  className="flex items-center gap-2 text-left"
                >
                  <span
                    className="text-xs"
                    style={{ color: b.color }}
                    aria-hidden
                  >
                    {b.icon}
                  </span>
                  <span className="text-[13px] font-medium text-stone-800 dark:text-stone-100">
                    {b.title}
                  </span>
                  <span className="text-[11px] text-stone-400">
                    {b.steps.filter((s) => statusOf(s.id) === 'done').length}/{b.steps.length}
                  </span>
                  <span className="text-[10px] text-stone-300 dark:text-zinc-600">
                    {open[b.id] ? '▾' : '▸'}
                  </span>
                </button>
                <div className="flex gap-1">
                  {b.chains.map((c) => (
                    <span
                      key={c}
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        (pipelineType === 'video' ? '视频' : '图文') === c
                          ? 'bg-brand-100 text-brand-700 dark:bg-brand-500/20 dark:text-brand-300'
                          : 'text-stone-300 dark:text-zinc-600'
                      }`}
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </div>

              {/* 叶子步骤 */}
              {open[b.id] && (
                <div className="mt-2.5 space-y-1.5 border-l border-dashed border-[var(--border-strong)] pl-3">
                  {b.steps.map((s) => {
                    const st = statusOf(s.id)
                    const style = STATUS_STYLE[st]
                    return (
                      <button
                        key={s.id}
                        onClick={() => onGo(s.route)}
                        onMouseEnter={() => setHover(s.id)}
                        onMouseLeave={() => setHover(null)}
                        className={`flex w-full items-start gap-2 rounded-[var(--r-sm)] px-2 py-1.5 text-left transition-colors ${
                          hover === s.id
                            ? 'bg-[var(--surface)] shadow-[var(--shadow-xs)]'
                            : 'hover:bg-[var(--surface)]'
                        }`}
                      >
                        <span
                          className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`}
                          title={style.label}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block text-[12px] font-medium text-stone-700 dark:text-stone-200">
                            {s.title}
                          </span>
                          <span className="block text-[11px] leading-relaxed text-stone-400">
                            {s.desc}
                          </span>
                        </span>
                        <span className="mt-0.5 shrink-0 text-[10px] text-stone-300 dark:text-zinc-600">
                          {style.label}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 反向环：数据回流 → 选题（思维导图能表达流程图表达不了的闭环） */}
      <div className="mt-4 flex items-center gap-2 rounded-[var(--r-md)] border border-dashed border-[var(--brand-300)] bg-brand-50/50 px-3 py-2 dark:bg-brand-500/5">
        <span className="text-[11px] text-brand-600 dark:text-brand-300">↺</span>
        <span className="text-[11px] text-stone-600 dark:text-stone-300">
          <strong className="font-medium">闭环</strong>
          ：数据看板的爆文要素会回流成新选题，形成持续增强
        </span>
        <button
          onClick={() => onGo('analytics')}
          className="ml-auto shrink-0 text-[11px] text-brand-600 underline-offset-2 hover:underline dark:text-brand-300"
        >
          去看
        </button>
      </div>
    </div>
  )
}
