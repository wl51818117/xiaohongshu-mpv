import type { ReactNode } from 'react'

/** 页面骨架：统一「阶段徽标 + 说明 + 规划内容」的结构。
 *
 * 用于尚未完全实现的页面：先把入口与规划讲清楚，
 * 避免出现空白页让人不知道下一步做什么。
 */
export function PageScaffold({
  stage,
  title,
  desc,
  plan,
  children,
}: {
  /** 开发阶段标记 */
  stage: string
  title: string
  desc: string
  /** 规划中的功能点 */
  plan: string[]
  children?: ReactNode
}) {
  return (
    <>
      <div className="mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-stone-900">{title}</h2>
          <span className="tag border border-amber-200 bg-amber-50 text-amber-700">
            {stage}
          </span>
        </div>
        <p className="mt-0.5 text-xs text-stone-500">{desc}</p>
      </div>

      {children}

      <div className="card p-5">
        <div className="mb-3 text-sm font-medium text-stone-900">规划中的功能</div>
        <ul className="space-y-2">
          {plan.map((item) => (
            <li key={item} className="flex gap-2.5 text-sm text-stone-600">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-stone-300" />
              <span className="leading-relaxed">{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
