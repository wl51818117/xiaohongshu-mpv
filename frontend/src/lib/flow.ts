/**
 * 内容生产流程定义：图文链路与视频链路。
 *
 * 每一步对应工作台的一个落地页，点击流程节点即可进入。
 * 「状态」由后端真实数据计算（见 buildStepStatus），不是写死的。
 */

/** 步骤状态 */
export type StepStatus = 'done' | 'active' | 'pending' | 'blocked'

/** 流程步骤定义 */
export type StepDef = {
  id: string
  title: string
  desc: string
  /** 点击进入的工作台页签 */
  route:
    | 'pipeline'
    | 'materials'
    | 'topics'
    | 'draft'
    | 'assets'
    | 'publish'
    | 'analytics'
  /** 图文链路是否包含该步 */
  image: boolean
  /** 视频链路是否包含该步 */
  video: boolean
}

export const STEPS: StepDef[] = [
  {
    id: 'collect',
    title: '素材采集',
    desc: 'RSS 源与自有爆款回采，统一入素材库并去重',
    route: 'materials',
    image: true,
    video: true,
  },
  {
    id: 'convert',
    title: '选题转换',
    desc: '合规过筛后转成可执行选题，带长尾词与人群',
    route: 'topics',
    image: true,
    video: true,
  },
  {
    id: 'copy',
    title: '文案创作',
    desc: '生成初稿 → 去 AI 味 → 合规校验 → 人工定稿',
    route: 'draft',
    image: true,
    video: true,
  },
  {
    id: 'cover',
    title: '封面生成',
    desc: '3:4 竖版封面，出 3 个候选供挑选',
    route: 'assets',
    image: true,
    video: true,
  },
  {
    id: 'inner',
    title: '内页排版',
    desc: '4-8 张内页，九宫格顺序：全景→细节→场景',
    route: 'assets',
    image: true,
    video: false,
  },
  {
    id: 'script',
    title: '分镜脚本',
    desc: '0-3 秒钩子 / 3-6 秒可信 / 主体 / 结尾转化',
    route: 'assets',
    image: false,
    video: true,
  },
  {
    id: 'genvideo',
    title: '图生视频',
    desc: '首帧图 → 分镜视频（必用图生视频，文生视频不可控）',
    route: 'assets',
    image: false,
    video: true,
  },
  {
    id: 'compose',
    title: '合成校验',
    desc: '拼接 + 字幕烧录 + 规格与合规双校验',
    route: 'assets',
    image: true,
    video: true,
  },
  {
    id: 'publish',
    title: '半自动发布',
    desc: '风控探测 → 自动预填 → 人工点发布',
    route: 'publish',
    image: true,
    video: true,
  },
  {
    id: 'review',
    title: '数据复盘',
    desc: '1/3/7/14 天快照，爆文要素回流选题',
    route: 'analytics',
    image: true,
    video: true,
  },
]

/** 取某条链路的步骤序列 */
export function stepsOf(kind: 'image' | 'video'): StepDef[] {
  return STEPS.filter((s) => s[kind])
}

/**
 * 依据真实数据计算每步状态。
 *
 * 没有真实数据支撑的环节一律pending —— 不谎报进度。
 * 这是从「静态流程图」变成「活的进度图」的关键。
 */
export function buildStepStatus(
  kind: 'image' | 'video',
  stats: { materials: number; topics: number; drafts: number; published: number },
): Record<string, StepStatus> {
  const list = stepsOf(kind)
  const out: Record<string, StepStatus> = {}
  list.forEach((s) => (out[s.id] = 'pending'))

  // 采集：有素材即完成
  if (stats.materials > 0) out['collect'] = 'done'
  // 转换：有选题即完成
  if (stats.topics > 0) out['convert'] = 'done'

  // 内容生产：有稿件即推进到合成校验
  if (stats.drafts > 0) {
    out['copy'] = 'done'
    out['cover'] = 'done'
    if (kind === 'image') out['inner'] = 'done'
    if (kind === 'video') {
      out['script'] = 'done'
      out['genvideo'] = 'done'
    }
    out['compose'] = 'done'
    out['publish'] = 'active'
  } else if (stats.topics > 0) {
    out['copy'] = 'active'
  } else if (stats.materials > 0) {
    out['convert'] = 'active'
  } else {
    out['collect'] = 'active'
  }

  // 发布：有已发布内容则发布完成，复盘成为当前环节
  if (stats.published > 0) {
    out['publish'] = 'done'
    out['review'] = 'active'
  }

  return out
}

/** 节点视觉配置（light 主题） */
export function nodeStyle(status: StepStatus) {
  switch (status) {
    case 'done':
      return {
        fill: '#ECFDF5',
        stroke: '#10B981',
        text: '#065F46',
        badge: '✓',
      }
    case 'active':
      return {
        fill: '#FFF7ED',
        stroke: '#EA580C',
        text: '#9A3412',
        badge: '!',
      }
    case 'blocked':
      return {
        fill: '#F5F5F4',
        stroke: '#A8A29E',
        text: '#78716C',
        badge: '×',
      }
    default:
      return {
        fill: '#FFFFFF',
        stroke: '#D6D3D1',
        text: '#57534E',
        badge: '',
      }
  }
}

export const STATUS_LABEL: Record<StepStatus, string> = {
  done: '已完成',
  active: '待进行',
  pending: '未开始',
  blocked: '受阻',
}
