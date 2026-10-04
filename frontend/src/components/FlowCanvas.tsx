import { useEffect, useMemo, useRef } from 'react'
import { Graph } from '@antv/x6'
import { nodeStyle, stepsOf, type StepStatus } from '../lib/flow'

type Props = {
  kind: 'image' | 'video'
  statuses: Record<string, StepStatus>
  onEnter: (route: string, stepId: string) => void
}

/** 节点尺寸与间距 */
const NODE_W = 168
const NODE_H = 66
const GAP_X = 62
const GAP_Y = 88

/** 单条链路的节点序列（两条链路节点数不同，纵向排布避免拥挤） */
function layout(kind: 'image' | 'video') {
  return stepsOf(kind).map((step, i) => ({
    ...step,
    x: GAP_X + i * (NODE_W + GAP_X),
    y: GAP_Y,
    index: i,
  }))
}

export function FlowCanvas({ kind, statuses, onEnter }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const onEnterRef = useRef(onEnter)
  onEnterRef.current = onEnter

  const nodes = useMemo(() => layout(kind), [kind])
  // 用 status 串进依赖，状态变化时重建，保证颜色与徽标刷新
  const statusKey = nodes.map((n) => statuses[n.id] ?? 'pending').join(',')

  useEffect(() => {
    if (!containerRef.current) return

    const graph = new Graph({
      container: containerRef.current,
      width: nodes.length * (NODE_W + GAP_X) + GAP_X,
      height: NODE_H + GAP_Y * 2,
      grid: false,
      panning: false,
      background: { color: '#FAFAF9' },
    })
    graphRef.current = graph

    // 节点：圆角矩形 + 状态色
    graph.addNodes(
      nodes.map((step) => {
        const status = statuses[step.id] ?? 'pending'
        const s = nodeStyle(status)
        return {
          id: step.id,
          x: step.x,
          y: step.y,
          width: NODE_W,
          height: NODE_H,
          shape: 'round-rect',
          attrs: {
            body: {
              fill: s.fill,
              stroke: s.stroke,
              strokeWidth: status === 'active' ? 2 : 1.5,
              rx: 10,
              ry: 10,
              // 当前步骤加重影，明确"下一步做什么"
              filter:
                status === 'active'
                  ? 'drop-shadow(0 0 6px rgba(234,88,12,0.28))'
                  : undefined,
            },
            label: {
              text: `${s.badge ? s.badge + ' ' : ''}${step.title}`,
              fill: s.text,
              fontSize: 13,
              fontWeight: 500,
              textVerticalAnchor: 'middle',
              textHorizontalAnchor: 'middle',
            },
          },
          // 描述文字放在节点下方
          data: { status, stepId: step.id, route: step.route },
          zIndex: 10,
        }
      }),
    )

    // 连线：箭头 + 未完成路径用虚线
    // 注意：直接用节点 id 锚定（不指定 port），最稳妥
    graph.addEdges(
      nodes.slice(0, -1).map((step, i) => {
        const next = nodes[i + 1]
        const curDone = (statuses[step.id] ?? 'pending') === 'done'
        const nextDone = (statuses[next.id] ?? 'pending') === 'done'
        const solid = curDone && nextDone
        return {
          id: `e-${step.id}-${next.id}`,
          source: { cell: step.id },
          target: { cell: next.id },
          connector: 'smooth',
          attrs: {
            line: {
              stroke: solid ? '#10B981' : '#D6D3D1',
              strokeWidth: 1.6,
              targetMarker: { name: 'block', width: 9, height: 7 },
              lineDash: solid ? undefined : '5 4',
            },
          },
          zIndex: 1,
        }
      }),
    )

    // 描述文字压在节点下方，便于阅读
    nodes.forEach((step) => {
      graph.addNode({
        id: `desc-${step.id}`,
        x: step.x,
        y: step.y + NODE_H + 6,
        width: NODE_W,
        height: 40,
        shape: 'rect',
        attrs: {
          body: { fill: 'transparent', stroke: 'transparent' },
          label: {
            text: wrapDesc(step.desc, 13),
            fill: '#A8A29E',
            fontSize: 11,
            textVerticalAnchor: 'top',
            textHorizontalAnchor: 'left',
            refDxy: '0, 2',
          },
        },
        zIndex: 5,
      })
    })

    // 点击节点 → 进入对应工作页
    graph.on('node:click', ({ node }) => {
      const data = node.getData() as { route?: string; stepId?: string }
      if (data?.route) onEnterRef.current(data.route, data.stepId ?? '')
    })

    // 悬停高亮，提示可点击
    graph.on('node:mouseenter', ({ node }) => {
      const d = node.getData() as { route?: string }
      if (d?.route) {
        containerRef.current!.style.cursor = 'pointer'
        node.attr('body/strokeWidth', 2.5)
      }
    })
    graph.on('node:mouseleave', ({ node }) => {
      if (containerRef.current) containerRef.current.style.cursor = 'default'
      const data = node.getData() as { status?: StepStatus }
      node.attr('body/strokeWidth', data?.status === 'active' ? 2 : 1.5)
    })

    // X6 2.x：挂载后自动渲染，这里等一帧确保容器尺寸就绪
    const raf = requestAnimationFrame(() => {
      graph.resize(1)
    })

    return () => {
      cancelAnimationFrame(raf)
      graph.dispose()
      graphRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, statusKey])

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
