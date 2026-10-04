import { useEffect, useMemo, useRef } from 'react'
import { Graph } from '@antv/x6'
import { nodeStyle, stepsOf, type StepStatus } from '../lib/flow'

type Props = {
  kind: 'image' | 'video'
  statuses: Record<string, StepStatus>
  onEnter: (route: string, stepId: string) => void
}

/** 节点尺寸与间距 */
const NODE_W = 170
const NODE_H = 62
const GAP_X = 64
const TOP_Y = 64

export function FlowCanvas({ kind, statuses, onEnter }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const onEnterRef = useRef(onEnter)
  onEnterRef.current = onEnter

  const nodes = useMemo(() => stepsOf(kind), [kind])

  // ── 初始化：仅在 kind 变化时重建画布 ────────────────────
  // 注意：不传 shape，用 X6 默认矩形。
  // 网上多数教程是 1.x 写法（shape:'rect' / 'round-rect'），
  // X6 3.x 已改为类式 API，传旧字符串会报
  // "Node with name 'round-rect' does not exist" 导致整页白屏。
  useEffect(() => {
    if (!containerRef.current) return

    const graph = new Graph({
      container: containerRef.current,
      width: nodes.length * (NODE_W + GAP_X) + GAP_X,
      height: TOP_Y + NODE_H + 70,
      grid: false,
      panning: false,
      background: { color: '#FAFAF9' },
    })
    graphRef.current = graph

    // 主节点
    nodes.forEach((step, i) => {
      graph.addNode({
        id: step.id,
        x: GAP_X + i * (NODE_W + GAP_X),
        y: TOP_Y,
        width: NODE_W,
        height: NODE_H,
        label: step.title,
        data: { route: step.route, stepId: step.id },
        zIndex: 10,
      })
    })

    // 说明文字（独立文本节点）
    nodes.forEach((step, i) => {
      graph.addNode({
        id: `desc-${step.id}`,
        x: GAP_X + i * (NODE_W + GAP_X),
        y: TOP_Y + NODE_H + 10,
        width: NODE_W,
        height: 44,
        label: wrapDesc(step.desc, 13),
        zIndex: 5,
      })
      // 说明文字设为不可交互，避免抢占点击
      const d = graph.getCellById(`desc-${step.id}`)
      if (d?.isNode?.()) d.setProp('zIndex', 0)
    })

    // 连线：直接用 cell id 锚定
    nodes.slice(0, -1).forEach((step, i) => {
      graph.addEdge({
        id: `e-${step.id}-${nodes[i + 1].id}`,
        source: { cell: step.id },
        target: { cell: nodes[i + 1].id },
        connector: 'smooth',
        attrs: {
          line: {
            stroke: '#D6D3D1',
            strokeWidth: 1.6,
            targetMarker: { name: 'block', width: 9, height: 7 },
            lineDash: '5 4',
          },
        },
        zIndex: 1,
      })
    })

    // 点击进入工作页
    graph.on('node:click', ({ node }) => {
      const d = node.getData() as { route?: string; stepId?: string }
      if (d?.route) onEnterRef.current(d.route, d.stepId ?? '')
    })
    graph.on('node:mouseenter', ({ node }) => {
      const d = node.getData() as { route?: string }
      if (!d?.route) return
      if (containerRef.current) containerRef.current.style.cursor = 'pointer'
    })
    graph.on('node:mouseleave', () => {
      if (containerRef.current) containerRef.current.style.cursor = 'default'
    })

    return () => {
      graph.dispose()
      graphRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  // ── 状态变化：只更新样式，不重建画布 ────────────────────
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return

    nodes.forEach((step) => {
      const status = statuses[step.id] ?? 'pending'
      const s = nodeStyle(status)
      const cell = graph.getCellById(step.id)
      if (!cell?.isNode?.()) return

      cell.setData({ ...(cell.getData() as object), status })
      cell.attr({
        body: {
          fill: s.fill,
          stroke: s.stroke,
          strokeWidth: status === 'active' ? 2 : 1.5,
          rx: 10,
          ry: 10,
        },
        label: {
          text: `${s.badge ? s.badge + ' ' : ''}${step.title}`,
          fill: s.text,
          fontSize: 13,
          fontWeight: 500,
        },
      })
    })

    // 说明文字去掉默认边框
    nodes.forEach((step) => {
      const d = graph.getCellById(`desc-${step.id}`)
      if (d?.isNode?.()) {
        d.attr({
          body: { fill: 'transparent', stroke: 'transparent' },
          label: { fill: '#A8A29E', fontSize: 11, textWrap: { width: NODE_W - 8 } },
        })
      }
    })

    // 连线：两端都完成才实线
    nodes.slice(0, -1).forEach((step, i) => {
      const edge = graph.getCellById(`e-${step.id}-${nodes[i + 1].id}`)
      if (!edge?.isEdge?.()) return
      const solid =
        (statuses[step.id] ?? 'pending') === 'done' &&
        (statuses[nodes[i + 1].id] ?? 'pending') === 'done'
      edge.attr({
        line: {
          stroke: solid ? '#10B981' : '#D6D3D1',
          lineDash: solid ? undefined : '5 4',
        },
      })
    })
  }, [statuses, nodes])

  return (
    <div className="overflow-x-auto pb-2">
      <div ref={containerRef} className="min-w-full" />
    </div>
  )
}

/** 描述文字按宽度折行 */
function wrapDesc(text: string, perLine: number): string {
  const lines: string[] = []
  let cur = ''
  for (const ch of text) {
    cur += ch
    if (cur.length >= perLine) {
      lines.push(cur)
      cur = ''
    }
  }
  if (cur) lines.push(cur)
  return lines.slice(0, 3).join('\n')
}
