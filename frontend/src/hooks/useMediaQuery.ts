import { useEffect, useState } from 'react'

/** 媒体查询 hook（SSR 安全）。
 *
 * 用于响应式降级：窄屏时 Agent 从并列侧栏改为浮层/全屏。
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia(query).matches
  })

  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches)
    setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}

/** 断点约定：< 1024px 视为窄屏，Agent 降级为浮层。 */
export const BREAKPOINT_NARROW = '(max-width: 1023px)'
