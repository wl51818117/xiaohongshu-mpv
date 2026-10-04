import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  api,
  type ConvertResult,
  type CollectResult,
  type FeedPreset,
  type KernelStatus,
  type Material,
  type PipelineStatus,
  type Draft,
  type Topic,
} from './lib/api'
import {
  DiffBadge,
  EmptyState,
  SectionTitle,
  StatCard,
  StatusBadge,
  StatusDot,
  ValueTypeBadge,
  Button,
} from './components/ui'
import { AgentChat } from './components/AgentChat'
import { BREAKPOINT_NARROW, useMediaQuery } from './hooks/useMediaQuery'
import { MindMap } from './components/MindMap'
import { PageScaffold } from './components/PageScaffold'
import { WritingDesk } from './components/WritingDesk'
import { AssetStudio } from './components/AssetStudio'
import { AnalyticsBoard } from './components/AnalyticsBoard'
import { KnowledgeBase } from './components/KnowledgeBase'
import { SettingsPanel } from './components/SettingsPanel'
import { AgentPanels } from './components/AgentPanels'
import { buildStepStatus, stepsOf, STEPS } from './lib/flow'
import { connectWorkbench, type ViewState, type WorkbenchOps } from './bridge/workbench-ops'

type Tab =
  | 'flow'
  | 'pipeline'
  | 'materials'
  | 'topics'
  | 'draft'
  | 'assets'
  | 'publish'
  | 'analytics'
  | 'knowledge'
  | 'dynamic'

/**
 * 导航顺序 = 内容生产顺序：
 * 流程图（总览）→ 流水线（采集）→ 素材库（原料）→ 选题库（选题）
 * → 稿件（文案）→ 素材工坊（图/视频）→ 发布队列→ 数据看板（复盘）
 * → 知识库（经验沉淀与回顾）
 */
const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: 'flow', label: '流程图', hint: '图文 / 视频' },
  { key: 'pipeline', label: '流水线', hint: '采集与转换' },
  { key: 'materials', label: '素材库', hint: '原始资讯' },
  { key: 'topics', label: '选题库', hint: '可执行选题' },
  { key: 'draft', label: '稿件', hint: '文案编辑' },
  { key: 'assets', label: '素材工坊', hint: '图片 / 视频' },
  { key: 'publish', label: '发布队列', hint: '半自动发布' },
  { key: 'analytics', label: '数据看板', hint: '复盘与爆文复用' },
  { key: 'knowledge', label: '知识库', hint: '踩坑与经验' },
  { key: 'dynamic', label: '自定义面板', hint: 'Agent 动态创建' },
]

export default function App() {
  const [tab, setTab] = useState<Tab>('flow')
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [kernel, setKernel] = useState<KernelStatus | null>(null)
  const [topics, setTopics] = useState<Topic[]>([])
  const [materials, setMaterials] = useState<Material[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null)

  // ── 流程图状态：图文与视频两条链路分开计算 ──
  // 状态来自后端真实数据；无数据支撑的环节一律 pending，不谎报进度
  const [draftStat, setDraftStat] = useState(0)
  const flowStats = {
    materials: status?.materials ?? 0,
    topics: status?.topics ?? 0,
    drafts: draftStat,
    published: 0,
  }
  /** 思维导图当前展示的链路（图文/视频） */
  const [pipelineType, setPipelineType] = useState<'image' | 'video'>('image')
  const imageStatus = useMemo(
    () => buildStepStatus('image', flowStats),
    [flowStats.materials, flowStats.topics, flowStats.drafts],
  )
  const videoStatus = useMemo(
    () => buildStepStatus('video', flowStats),
    [flowStats.materials, flowStats.topics, flowStats.drafts],
  )

  // ── Agent 侧栏状态 ──
  // 桌面端：展开时占固定宽度（380px），与主内容区并列
  // 窄屏端：降级为浮层，默认收起，避免遮挡主内容
  const isNarrow = useMediaQuery(BREAKPOINT_NARROW)
  const [agentOpen, setAgentOpen] = useState(false)

  // ── bridge 通道（hbridge v2.1）─────────────────────────
  // 让 Agent 能操作界面（切页签/筛选/开选题），而不只是调后端 API
  const [bridgeState, setBridgeState] = useState<
    'idle' | 'connecting' | 'ready' | 'failed'
  >('idle')
  const [topicFilter, setTopicFilter] = useState<{
    status?: string
    keyword?: string
  }>({})
  const [selectedTopicId, setSelectedTopicId] = useState<number | null>(null)
  const [presets, setPresets] = useState<FeedPreset[]>([])
  const [currentDraft, setCurrentDraft] = useState<Draft | null>(null)
  // 侧栏折叠状态（localStorage 持久化）
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem('wb-sidebar-collapsed') === '1',
  )
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 当前写作的稿件 id（从选题库转稿件时确定）
  const [activeDraftId, setActiveDraftId] = useState<number | null>(null)
  // Agent 动态面板的当前 id
  const [panelTarget, setPanelTarget] = useState<string | null>(null)
  const [presetKey, setPresetKey] = useState<string>('')

  // 侧栏折叠状态持久化
  useEffect(() => {
    localStorage.setItem('wb-sidebar-collapsed', sidebarCollapsed ? '1' : '0')
  }, [sidebarCollapsed])

  // 窄屏时默认收起，避免一进来就被浮层挡住主内容
  useEffect(() => {
    if (isNarrow) setAgentOpen(false)
  }, [isNarrow])

  /** 当前视图状态：Agent 通过 read_workbench_state 读取 */
  const readView = useCallback((): ViewState => {
    return {
      tab,
      topicFilter,
      selectedTopicId,
      stats: {
        materials: status?.materials ?? 0,
        topics: status?.topics ?? 0,
        drafts: draftStat,
      },
    }
  }, [tab, topicFilter, selectedTopicId, status, draftStat])

  /** 选题库筛选结果（bridge 的 filter_topics 会改topicFilter）*/
  const visibleTopics = useMemo(() => {
    const kw = topicFilter.keyword?.trim().toLowerCase()
    return topics.filter((t) => {
      if (topicFilter.status && t.status !== topicFilter.status) return false
      if (kw && !t.title.toLowerCase().includes(kw)) return false
      return true
    })
  }, [topics, topicFilter])

  /** 接通 bridge：注册工作台能力，让模型可调用 */
  useEffect(() => {
    let disposed = false

    const ops: WorkbenchOps = {
      gotoTab: (t) => {
        if (!disposed) setTab(t as Tab)
      },
      setTopicFilter: (f) => {
        if (!disposed) setTopicFilter(f)
      },
      selectTopic: (id) => {
        if (!disposed) setSelectedTopicId(id)
      },
      openAgent: () => {
        if (!disposed) setAgentOpen(true)
      },
      // 元能力需要的回调
      openPanel: (id: string) => {
        if (!disposed) {
          setPanelTarget(id)
          setTab('dynamic')
        }
      },
      refresh: () => {
        if (!disposed) void refresh()
      },
      tabs: TABS.map((t) => ({ key: t.key, label: t.label })),
      readView,
    }

    setBridgeState('connecting')
    connectWorkbench(ops)
      .then(() => {
        if (!disposed) setBridgeState('ready')
      })
      .catch(() => {
        // bridge 不可用不影响主流程：Agent 对话仍走原有 /api/agent/chat
        if (!disposed) setBridgeState('failed')
      })

    return () => {
      disposed = true
    }
  }, [readView])



  /** 素材库：把一条素材直接转成选题。 */
  const addMaterialToTopic = async (m: Material) => {
    setBusy(`mat-${m.id}`)
    try {
      const r = await fetch(`/api/pipeline/materials/${m.id}/to-topic`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ material_id: m.id }),
      }).then((x) => x.json())
      if (r.created) {
        notify('ok', `已加入选题库 #${r.id}：${(r.title || '').slice(0, 24)}`)
      } else {
        notify('err', '该素材已在选题库中，未重复创建')
      }
      void refresh()
    } catch (e) {
      notify('err', `加入选题失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  /** 选题库：删除选题。 */
  const removeTopic = async (t: Topic) => {
    if (!confirm(`确定删除选题「${t.title.slice(0, 20)}」？\n\n若该选题已有稿件，会一并删除。`)) {
      return
    }
    setBusy(`del-${t.id}`)
    try {
      const r = await fetch(`/api/pipeline/topics/${t.id}`, {
        method: 'DELETE',
      }).then((x) => x.json())
      notify(
        'ok',
        r.deleted_drafts > 0
          ? `已删除选题及其 ${r.deleted_drafts} 篇稿件`
          : '已删除选题',
      )
      void refresh()
    } catch (e) {
      notify('err', `删除失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  /** 从选题转稿件并进入写作台。
   *
   * 流程修正（王哥指出）：稿件不是独立页，必须从选题派生。
   * 这里先调 /from-topic 拿到带上下文的稿件，再跳转。
   */
  const startDraftFromTopic = async (topic: Topic) => {
    setBusy(`draft-${topic.id}`)
    try {
      const r = await fetch('/api/drafts/from-topic', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ topic_id: topic.id }),
      }).then((x) => x.json())
      if (!r.ok && r.detail) {
        notify('err', typeof r.detail === 'string' ? r.detail : '转稿件失败')
        return
      }
      setSelectedTopicId(topic.id)
      setActiveDraftId(r.draft_id)
      setTab('draft')
      notify('ok', r.created ? `已创建稿件 #${r.draft_id}` : `打开已有稿件 #${r.draft_id}`)
      void refresh()
    } catch (e) {
      notify('err', `转稿件失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const refresh = useCallback(async () => {
    try {
      const [s, t, m, d] = await Promise.all([
        api.status(),
        api.topics(),
        api.materials(),
        fetch('/api/drafts/stats/summary')
          .then((r) => r.json())
          .catch(() => ({ total: 0 })),
      ])
      setStatus(s)
      setTopics(t.items)
      setMaterials(m.items)
      setDraftStat(d.total ?? 0)
      // 取最近一篇稿件供素材工坊使用
      const dl = await fetch('/api/drafts?limit=1').then((x) => x.json()).catch(() => null)
      if (dl?.items?.length) {
        setCurrentDraft(await fetch(`/api/drafts/${dl.items[0].id}`).then((x) => x.json()).catch(() => null))
      }
    } catch (e) {
      setToast({ kind: 'err', msg: `加载失败：${(e as Error).message}` })
    }
  }, [])

  useEffect(() => {
    refresh()
    api.kernel().then(setKernel).catch(() => setKernel({ ok: false }))
    api.feeds().then((r) => {
      setPresets(r.presets)
      setPresetKey((k) => k || r.presets[0]?.key || '')
    }).catch(() => setPresets([]))
  }, [refresh])

  const notify = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg })
    setTimeout(() => setToast(null), 4000)
  }

  /** 点击流程节点 → 跳到对应工作页 */
  const enterStep = (route: string) => {
    const valid: Tab[] = [
      'pipeline',
      'materials',
      'topics',
      'draft',
      'assets',
      'publish',
      'analytics',
    ]
    if (valid.includes(route as Tab)) {
      setTab(route as Tab)
    } else {
      notify('err', `该环节尚未开放：${route}`)
    }
  }

  const runCollect = async (limit: number) => {
    setBusy('collect')
    try {
      const r: CollectResult = await api.collect(limit, presetKey || undefined)
      const total = r.per_feed.reduce((a, b) => a + b.added, 0)
      notify(
        'ok',
        `采集完成：新增 ${r.added} 条（抓取 ${r.fetched}，重复 ${r.duplicated}），` +
          r.per_feed.map((f) => `${f.name}${f.fetched ? '' : '(无响应)'}`).join(' '),
      )
      void total
      await refresh()
    } catch (e) {
      notify('err', `采集失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const runConvert = async () => {
    setBusy('convert')
    try {
      const r: ConvertResult = await api.convert(20)
      const rejectInfo =
        r.rejected > 0 ? `，合规拦截 ${r.rejected} 条` : ''
      notify(
        'ok',
        `转换完成：处理 ${r.processed}，成功 ${r.converted}，拦截 ${r.rejected}${rejectInfo}`,
      )
      await refresh()
    } catch (e) {
      notify('err', `转换失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const runAll = async () => {
    setBusy('all')
    try {
      const c = await api.collect(3, presetKey || undefined)
      const v = await api.convert(20)
      notify(
        'ok',
        `全流程完成：采集 ${c.added} 条 → 转换 ${v.converted} 条选题` +
          (v.rejected ? `（合规拦截 ${v.rejected}）` : ''),
      )
      await refresh()
    } catch (e) {
      notify('err', `执行失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="relative flex h-full overflow-hidden bg-[var(--bg)]">
      {/* ── 左侧导航：紫色渐变宽栏（可折叠成图标栏） ── */}
      <aside
        className={`${
          isNarrow ? 'hidden' : 'flex'
        } ${
          sidebarCollapsed ? 'w-[68px] items-center' : 'w-[220px] items-stretch'
        } shrink-0 flex-col bg-gradient-to-b from-[var(--grad-sidebar-from)] to-[var(--grad-sidebar-to)] py-4 transition-[width] duration-200`}
      >
        {/* Logo + 标题 */}
        <div
          className={`mb-4 flex items-center gap-2.5 px-4 ${
            sidebarCollapsed ? 'justify-center px-0' : ''
          }`}
        >
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 7.5A2.5 2.5 0 016.5 5h11A2.5 2.5 0 0120 7.5v9a2.5 2.5 0 01-2.5 2.5h-11A2.5 2.5 0 014 16.5v-9z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
              <path
                d="M8 9.5h8M8 13h5"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </div>
          {!sidebarCollapsed && (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-white">
                电商工作台
              </div>
              <div className="truncate text-[11px] text-white/60">
                小红书 · MVP
              </div>
            </div>
          )}
        </div>

        {/* 导航：展开时带文字，折叠时只留图标 */}
        <nav
          className={`flex flex-1 flex-col gap-1 px-3 ${
            sidebarCollapsed ? 'items-center px-2' : ''
          }`}
        >
          {TABS.map((t) => {
            const active = tab === t.key
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                title={`${t.label} · ${t.hint}`}
                aria-label={t.label}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-3 rounded-xl transition-all duration-200 ${
                  sidebarCollapsed
                    ? 'h-11 w-11 justify-center'
                    : 'px-3 py-2.5'
                } ${
                  active
                    ? 'bg-white text-[var(--grad-sidebar-from)] shadow-lg'
                    : 'text-white/75 hover:bg-white/15 hover:text-white'
                }`}
              >
                <span className="shrink-0">
                  <TabIcon tab={t.key} />
                </span>
                {!sidebarCollapsed && (
                  <span className="min-w-0 text-left">
                    <span className="block truncate text-sm font-medium">
                      {t.label}
                    </span>
                    <span
                      className={`block truncate text-[11px] ${
                        active ? 'text-stone-400' : 'text-white/50'
                      }`}
                    >
                      {t.hint}
                    </span>
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        {/* 底部：状态 + 设置 + 折叠 */}
        <div
          className={`mt-3 border-t border-white/15 pt-3 ${
            sidebarCollapsed ? 'flex flex-col items-center gap-2 px-2' : 'px-3'
          }`}
        >
          {!sidebarCollapsed && (
            <div className="mb-2 space-y-1 px-1">
              <div className="flex items-center gap-2 text-[11px] text-white/70">
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    kernel?.ok ? 'bg-emerald-400' : 'bg-white/25'
                  }`}
                />
                <span className="truncate">
                  内核{kernel?.ok ? '在线' : '离线'}
                  {kernel?.tools?.length ? ` · ${kernel.tools.length}工具` : ''}
                </span>
              </div>
              <div className="flex items-center gap-2 text-[11px] text-white/70">
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    bridgeState === 'ready'
                      ? 'bg-cyan-300'
                      : bridgeState === 'failed'
                        ? 'bg-white/25'
                        : 'bg-amber-300'
                  }`}
                />
                <span className="truncate">
                  {bridgeState === 'ready'
                    ? '界面可被 Agent 操作'
                    : bridgeState === 'connecting'
                      ? '桥接中'
                      : '桥接未启用'}
                </span>
              </div>
            </div>
          )}

          {/* 设置入口 */}
          <button
            onClick={() => setSettingsOpen(true)}
            title="设置"
            aria-label="设置"
            className={`flex items-center gap-3 rounded-xl py-2.5 text-white/75 transition-colors hover:bg-white/15 hover:text-white ${
              sidebarCollapsed
                ? 'h-11 w-11 justify-center'
                : 'w-full px-3'
            }`}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
              <path
                d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H2a2 2 0 110-4h.09A1.65 1.65 0 003.6 8a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H8a1.65 1.65 0 001-1.51V2a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V8a1.65 1.65 0 001.51 1H22a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            {!sidebarCollapsed && (
              <span className="text-sm font-medium">设置</span>
            )}
          </button>

          {/* 折叠切换 */}
          <button
            onClick={() => setSidebarCollapsed((c) => !c)}
            title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
            aria-label={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
            className={`flex items-center gap-3 rounded-xl py-2.5 text-white/75 transition-colors hover:bg-white/15 hover:text-white ${
              sidebarCollapsed
                ? 'h-11 w-11 justify-center'
                : 'w-full px-3'
            }`}
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              className={`shrink-0 transition-transform duration-200 ${
                sidebarCollapsed ? 'rotate-180' : ''
              }`}
            >
              <path
                d="M15 18l-6-6 6-6"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            {!sidebarCollapsed && (
              <span className="text-sm font-medium">收起</span>
            )}
          </button>
        </div>
      </aside>

      {/* ── 主区 ── */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 顶栏 */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--border)] bg-white/80 px-6 backdrop-blur dark:bg-zinc-900/70">
          {isNarrow && (
            <span className="text-sm font-semibold text-stone-900 dark:text-stone-50">
              {TABS.find((t) => t.key === tab)?.label}
            </span>
          )}
          {!isNarrow && (
            <div>
              <div className="text-sm font-semibold text-stone-900 dark:text-stone-50">
                {TABS.find((t) => t.key === tab)?.label}
              </div>
              <div className="text-[11px] text-stone-400">
                {TABS.find((t) => t.key === tab)?.hint}
              </div>
            </div>
          )}

          <div className="flex-1" />

          {/* 状态徽章 */}
          <span className="hidden items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 sm:inline-flex dark:bg-brand-500/15 dark:text-brand-300">
            <span className="h-1.5 w-1.5 rounded-full bg-brand-500" />
            {kernel?.ok ? '内核在线' : '内核离线'}
            {kernel?.tools?.length ? ` · ${kernel.tools.length} 工具` : ''}
          </span>
          <span className="hidden items-center gap-1.5 rounded-full bg-cyan-50 px-2.5 py-1 text-[11px] font-medium text-cyan-700 sm:inline-flex dark:bg-cyan-500/15 dark:text-cyan-300">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-500" />
            {bridgeState === 'ready' ? '界面可被 Agent 操作' : '桥接未启用'}
          </span>

          {/* 窄屏时的 Agent 入口 */}
          {isNarrow && (
            <Button
              variant="primary"
              size="sm"
              onClick={() => setAgentOpen(true)}
              aria-label="打开 Agent"
            >
              Agent
            </Button>
          )}
        </header>

        {/* ── 主内容区 ── */}
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-6 py-6">
            {/* 状态概览：四色渐变卡，对齐参考稿的功能卡视觉 */}
            {status && (
              <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                <StatCard index={0} label="素材" value={status.materials} />
                <StatCard index={1} label="选题" value={status.topics} />
                <StatCard
                  index={2}
                  label="待转换"
                  value={status.unconverted_materials}
                  hint={status.unconverted_materials > 0 ? '需跑转换' : '已清空'}
                />
                <StatCard index={3} label="可执行" value={status.available_topics} />
                <StatCard index={0} label="稿件" value={draftStat} />
              </div>
            )}

          {/* ── 流程图主页：图文与视频两条链路分开 ── */}
          {tab === 'flow' && (
            <>
              <SectionTitle
                title="内容生产流程"
                desc="点击任一环节直接进入对应工作页。状态由真实数据计算，虚线表示尚未完成。"
              />

              {/* 思维导图：树状表达分支与汇聚，比流程图更适合看全貌 */}
              <MindMap
                statusOf={(id) =>
                  (pipelineType === 'video' ? videoStatus : imageStatus)[
                    id as keyof typeof imageStatus
                  ] ?? 'pending'
                }
                onGo={(route) => enterStep(route)}
                pipelineType={pipelineType}
              />

              {/* 两条链路的分工（保留：这是决策性信息，导图不替代） */}
              <div className="card p-5">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-700">
                      图文链路
                    </span>
                    <span className="text-xs text-stone-400">
                      {stepsOf('image').length} 个环节 · 生命周期约 15-20 天
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700">
                      视频链路
                    </span>
                    <span className="text-xs text-stone-400">
                      {stepsOf('video').length} 个环节 · 长尾可达 90 天
                    </span>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  {(['image', 'video'] as const).map((kind) => (
                    <button
                      key={kind}
                      onClick={() => setPipelineType(kind)}
                      className={`rounded-[var(--r-md)] border px-3 py-2.5 text-left transition-colors ${
                        pipelineType === kind
                          ? 'border-brand-300 bg-brand-50/60 dark:border-brand-500/40 dark:bg-brand-500/10'
                          : 'border-[var(--border)] hover:border-[var(--border-strong)]'
                      }`}
                    >
                      <div className="mb-1.5 flex items-center justify-between">
                        <span className="text-[12px] font-medium text-stone-700 dark:text-stone-200">
                          {kind === 'image' ? '图文抢搜索流量' : '视频冲推荐流量'}
                        </span>
                        <span className="text-[10px] text-stone-400">
                          {kind === 'image' ? '15-20天' : '90天长尾'}
                        </span>
                      </div>
                      <div className="space-y-1">
                        {(kind === 'image' ? imageStatus : videoStatus) &&
                          Object.entries(kind === 'image' ? imageStatus : videoStatus).map(
                            ([sid, st]) => (
                              <div
                                key={sid}
                                className="flex items-center gap-1.5 text-[11px]"
                              >
                                <span
                                  className={`h-1 w-1 shrink-0 rounded-full ${
                                    st === 'done'
                                      ? 'bg-emerald-500'
                                      : st === 'active'
                                        ? 'bg-amber-500'
                                        : st === 'blocked'
                                          ? 'bg-red-500'
                                          : 'bg-stone-300 dark:bg-zinc-600'
                                  }`}
                                />
                                <span className="text-stone-500 dark:text-stone-400">
                                  {STEPS.find((x) => x.id === sid)?.title ?? sid}
                                </span>
                              </div>
                            ),
                          )}
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-relaxed text-amber-800">
                <strong>两条链路的分工</strong>：图文抢搜索流量（生命周期短但搜索承接好），
                视频冲推荐流量（初始曝光高约 30%、长尾 90 天）。
                最优策略是双轨并行，<strong>图文爆款可二次改编成视频二次分发</strong>。
              </div>
            </>
          )}

          {/* ── 稿件页：选题写作台（必须从选题库进入） ── */}
          {tab === 'draft' && (
            <WritingDesk
              topicId={selectedTopicId}
              draftId={activeDraftId}
              onNotify={notify}
              onChanged={refresh}
              onBack={() => setTab('topics')}
            />
          )}

          {/* ── Agent 动态面板 ── */}
          {tab === 'dynamic' && (
            <AgentPanels current={panelTarget} onOpen={setPanelTarget} />
          )}

          {tab === 'assets' && (
            <AssetStudio
              draft={currentDraft}
              onChanged={refresh}
              onNotify={notify}
            />
          )}

          {/* ── 发布队列 ── */}
          {tab === 'publish' && (
            <PageScaffold
              stage="P5"
              title="发布队列"
              desc="半自动发布：风控探测 → 自动预填 → 人工点发布"
              plan={[
                'botDetect 前置探测：动页面前先看有无登录/验证码/风控',
                'Playwright 自动预填：正文、标签、图片上传',
                '人工确认闸门：绝不 AI 全自动提交（平台封禁 AI 托管账号）',
                '发布后 10 分钟收录自查：用小号搜笔记全称',
                'AI 声明勾选：发布页【内容类型声明】必填项',
                '发布记录与审计：谁在何时发了什么',
              ]}
            />
          )}

          {/* ── 数据看板（已实现） ── */}
          {tab === 'analytics' && (
            <AnalyticsBoard
              draftId={currentDraft?.id ?? null}
              onNotify={notify}
              onChanged={refresh}
            />
          )}

          {tab === 'knowledge' && <KnowledgeBase onNotify={notify} />}

          {tab === 'topics' && (
            <>
              <SectionTitle
                title="选题库"
                desc="由素材经合规过筛后转换而来，可直接进入建稿"
                action={
                  <div className="flex items-center gap-2">
                    <input
                      value={topicFilter.keyword ?? ''}
                      onChange={(e) =>
                        setTopicFilter((f) => ({ ...f, keyword: e.target.value }))
                      }
                      placeholder="按标题筛选"
                      className="field !min-h-[34px] !w-32 !py-1.5 !text-xs"
                    />
                    <select
                      value={topicFilter.status ?? ''}
                      onChange={(e) =>
                        setTopicFilter((f) => ({
                          ...f,
                          status: e.target.value || undefined,
                        }))
                      }
                      className="field !min-h-[34px] !py-1.5 !text-xs"
                    >
                      <option value="">全部状态</option>
                      <option value="pooled">可执行</option>
                      <option value="claimed">建稿中</option>
                      <option value="done">已成稿</option>
                      <option value="archived">已归档</option>
                    </select>
                    {(topicFilter.status || topicFilter.keyword) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setTopicFilter({})}
                      >
                        清除
                      </Button>
                    )}
                  </div>
                }
              />
              {visibleTopics.length === 0 ? (
                <EmptyState
                  title={
                    topics.length === 0 ? '选题库还是空的' : '没有匹配的选题'
                  }
                  hint={
                    topics.length === 0
                      ? '去「流水线」页跑一次采集与转换'
                      : '试试清除筛选条件'
                  }
                />
              ) : (
                <div className="space-y-2.5">
                  {visibleTopics.map((t) => (
                    <div
                      key={t.id}
                      className={`card animate-in px-4 py-3.5 transition-colors ${
                        selectedTopicId === t.id
                          ? 'border-orange-400 ring-1 ring-orange-200'
                          : 'hover:border-stone-300'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium leading-snug text-stone-900">
                            {t.title}
                          </div>
                          <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            <ValueTypeBadge value={t.value_type} />
                            {t.persona && (
                              <span className="tag bg-stone-100 text-stone-600">
                                {t.persona}
                              </span>
                            )}
                            {t.differentiation.map((d) => (
                              <DiffBadge key={d} label={d} />
                            ))}
                            <StatusBadge status={t.status} />
                          </div>
                          <div className="mt-2 flex items-center gap-3 text-xs text-stone-400">
                            <span>
                              长尾词：
                              <span className="text-stone-600">
                                {t.keyword_target || '未提取'}
                              </span>
                            </span>
                            {t.source_name && <span>来源：{t.source_name}</span>}
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-col gap-1">
                          <div className="flex gap-1">
                            <Button
                              variant="primary"
                              size="sm"
                              loading={busy === `draft-${t.id}`}
                              disabled={busy !== null}
                              onClick={() => void startDraftFromTopic(t)}
                            >
                              {busy === `draft-${t.id}` ? '转稿中' : '写稿'}
                            </Button>
                            <Button
                              variant="danger"
                              size="sm"
                              disabled={busy !== null}
                              onClick={() => void removeTopic(t)}
                              aria-label={`删除选题 ${t.title}`}
                              title="删除该选题"
                            >
                              删
                            </Button>
                          </div>
                          {selectedTopicId === t.id && (
                            <span className="text-center text-[10px] text-orange-600">
                              当前
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'materials' && (
            <>
              <SectionTitle
                title="素材库"
                desc="RSS 等公开源采集的原始资讯，保留溯源链接"
              />
              {materials.length === 0 ? (
                <EmptyState title="素材库还是空的" hint="去「流水线」页执行采集" />
              ) : (
                <div className="space-y-2">
                  {materials.map((m) => (
                    <div
                      key={m.id}
                      className="card card-hover flex items-center gap-3 px-4 py-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-stone-800">
                          {m.title}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs text-stone-400">
                          <span>{m.source_name}</span>
                          {m.own_flag && (
                            <span className="tag bg-emerald-50 text-emerald-700">
                              自有
                            </span>
                          )}
                        </div>
                      </div>
                      <span
                        className={`tag shrink-0 ${
                          m.has_topic
                            ? 'bg-emerald-50 text-emerald-700'
                            : 'bg-amber-50 text-amber-700'
                        }`}
                      >
                        {m.has_topic ? '已转选题' : '待转换'}
                      </span>
                      <Button
                        variant="primary"
                        size="sm"
                        className="shrink-0"
                        loading={busy === `mat-${m.id}`}
                        disabled={busy !== null}
                        onClick={() => void addMaterialToTopic(m)}
                      >
                        {busy === `mat-${m.id}` ? '处理中' : '加入选题'}
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'pipeline' && (
            <>
              <SectionTitle
                title="流水线"
                desc="三步跑通：RSS 采集 → 选题转换 → 进入选题库"
              />

              <div className="card p-5">
                <ol className="space-y-4">
                  {[
                    {
                      n: 1,
                      t: 'RSS 采集入库',
                      d: '抓取公开源，清洗正文，按内容指纹去重',
                    },
                    {
                      n: 2,
                      t: '素材转选题',
                      d: '合规黑名单过筛 → 抽长尾词 → 标人群与差异化维度',
                    },
                    {
                      n: 3,
                      t: '进入选题库',
                      d: '可执行选题，供后续建稿使用',
                    },
                  ].map((s) => (
                    <li key={s.n} className="flex gap-3">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-stone-900 text-xs font-medium text-white">
                        {s.n}
                      </span>
                      <div>
                        <div className="text-sm font-medium text-stone-900">
                          {s.t}
                        </div>
                        <div className="mt-0.5 text-xs text-stone-500">{s.d}</div>
                      </div>
                    </li>
                  ))}
                </ol>

                <div className="mt-5">
                  <label className="field-label">赛道预设</label>
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={presetKey}
                      onChange={(e) => setPresetKey(e.target.value)}
                      className="field !w-auto !min-w-[200px]"
                      aria-label="选择赛道预设"
                    >
                      {presets.map((p) => (
                        <option key={p.key} value={p.key}>
                          {p.label}（{p.count} 源）
                        </option>
                      ))}
                    </select>
                    {presetKey && (
                      <span className="text-xs text-stone-400">
                        {presets.find((p) => p.key === presetKey)?.desc}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[11px] text-stone-400">
                    换赛道只需改 backend/data/feeds.json，不用动代码。
                  </p>
                </div>

                <div className="mt-5 flex flex-wrap gap-2 border-t border-stone-200 pt-4">
                  <Button
                    variant="default"
                    size="sm"
                    loading={busy === 'collect'}
                    disabled={busy !== null}
                    onClick={() => runCollect(3)}
                  >
                    {busy === 'collect' ? '采集中…' : '仅采集（每源 3 条）'}
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    loading={busy === 'convert'}
                    disabled={busy !== null}
                    onClick={() => runConvert()}
                  >
                    {busy === 'convert' ? '转换中…' : '仅转换'}
                  </Button>
                  {/* 注：原来是 btn-accent，但 CSS 里从没定义过这个类，
                      按钮其实没主色样式。改用 primary 让主路径更醒目。 */}
                  <Button
                    variant="primary"
                    size="sm"
                    loading={busy === 'all'}
                    disabled={busy !== null}
                    onClick={runAll}
                  >
                    {busy === 'all' ? '执行中…' : '一键跑完整流程'}
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    disabled={busy !== null}
                    onClick={refresh}
                  >
                    刷新
                  </Button>
                </div>
              </div>

              {kernel && (
                <div className="card mt-4 p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="t-section">内核工具清单</div>
                    <StatusDot
                      online={kernel.ok}
                      label={kernel.ok ? '在线' : '离线'}
                    />
                  </div>
                  {kernel.ok ? (
                    <>
                      <div className="mb-3 text-xs text-stone-500">
                        模型：
                        {kernel.model?.provider ?? '-'} /{' '}
                        {kernel.model?.model ?? '-'}
                        {kernel.sessions !== undefined &&
                          `　会话：${kernel.sessions}`}
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {kernel.tools?.map((t) => (
                          <span
                            key={t.name}
                            className="tag border border-stone-200 bg-stone-50 font-mono text-stone-600"
                          >
                            {t.name}
                          </span>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="text-xs text-stone-500">
                      内核未启动。启动方式：在
                      <code className="mx-1 rounded bg-stone-100 px-1">
                        E:\Ai-workbuddy\提取harness
                      </code>
                      双击 <code className="rounded bg-stone-100 px-1">start-all.cmd</code>
                      。不影响采集与选题功能。
                    </div>
                  )}
                </div>
              )}
            </>
          )}
          </div>
        </main>
      </div>

      {/* ── Agent 侧栏 ──
          桌面端（≥1024px）：与主内容区并列的固定列，不占用主内容宽度
          窄屏（<1024px）：降级为右侧浮层 + 遮罩，不挤压主内容
      */}
      {!isNarrow && agentOpen && (
        <aside className="w-[380px] shrink-0 animate-in border-l border-[var(--border)] bg-white dark:bg-zinc-900">
          <AgentChat
            online={kernel?.ok ?? false}
            onNotify={notify}
            onClose={() => setAgentOpen(false)}
          />
        </aside>
      )}

      {/* 窄屏浮层：全屏高度 + 遮罩，点击遮罩或关闭按钮退出 */}
      {isNarrow && agentOpen && (
        <>
          <div
            className="fixed inset-0 z-30 bg-stone-900/40 backdrop-blur-sm"
            onClick={() => setAgentOpen(false)}
            aria-hidden
          />
          <aside
            className="fixed inset-y-0 right-0 z-40 w-full max-w-[420px]
                       animate-in border-l border-[var(--border)] bg-white shadow-2xl
                       dark:bg-zinc-900"
          >
            <AgentChat
              online={kernel?.ok ?? false}
              onNotify={notify}
              onClose={() => setAgentOpen(false)}
            />
          </aside>
        </>
      )}

      {/* ── 收起态入口按钮（桌面端固定右下角） ── */}
      {!isNarrow && !agentOpen && (
        <button
          onClick={() => setAgentOpen(true)}
          aria-label="展开 Agent 侧栏"
          className="fixed bottom-5 right-5 z-30 flex items-center gap-2 rounded-full
                     bg-gradient-to-br from-[var(--grad-purple-from)] to-[var(--grad-purple-to)]
                     px-4 py-2.5 text-sm font-medium text-white shadow-lg
                     transition-all hover:brightness-110 hover:shadow-xl"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path
              d="M8 2.5C4.75 2.5 2.5 4.4 2.5 6.9c0 1.5.8 2.85 2.1 3.7l-.4 2.2 2.4-1.4c.47.08.95.12 1.4.12 3.25 0 5.5-1.9 5.5-4.62S11.25 2.5 8 2.5Z"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
          </svg>
          Agent
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              kernel?.ok ? 'bg-emerald-400' : 'bg-stone-500'
            }`}
          />
        </button>
      )}

      {/* ── 设置面板 ── */}
      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onNotify={notify}
        onSaved={refresh}
      />

      {/* ── 提示条 ──
          定位在主内容区中心：Agent 展开时不会互相遮挡
      */}
      {toast && (
        <div className="pointer-events-none fixed bottom-5 left-1/2 z-50 -translate-x-1/2">
          <div
            className={`animate-in max-w-lg rounded-lg px-4 py-2.5 text-sm shadow-lg ${
              toast.kind === 'ok'
                ? 'bg-stone-900 text-white dark:bg-zinc-800'
                : 'bg-red-600 text-white'
            }`}
          >
            {toast.msg}
          </div>
        </div>
      )}
    </div>
  )
}

/** 侧栏导航图标：统一 24×24 线性风格，stroke 1.8 */
function TabIcon({ tab }: { tab: string }) {
  const p = {
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    fill: 'none',
  }
  const paths: Record<string, ReactNode> = {
    // 流程图：节点连线
    flow: (
      <>
        <rect x="3" y="4" width="6" height="5" rx="1.5" {...p} />
        <rect x="15" y="4" width="6" height="5" rx="1.5" {...p} />
        <rect x="9" y="16" width="6" height="5" rx="1.5" {...p} />
        <path d="M9 6.5h6M6 9v4.5h6" {...p} />
      </>
    ),
    // 流水线：齿轮 + 箭头
    pipeline: (
      <>
        <path d="M4 7h5M13 7h7M4 17h9M17 17h3" {...p} />
        <circle cx="11" cy="7" r="2" {...p} />
        <circle cx="15" cy="17" r="2" {...p} />
      </>
    ),
    // 素材库：堆叠
    materials: (
      <>
        <rect x="4" y="4" width="12" height="12" rx="2" {...p} />
        <path d="M8 20h10a2 2 0 002-2V8" {...p} />
      </>
    ),
    // 选题库：灯泡
    topics: (
      <>
        <path d="M9 18h6M10 21h4" {...p} />
        <path d="M12 3a6 6 0 00-3.5 10.9c.5.4.8 1 .9 1.6h5.2c.1-.6.4-1.2.9-1.6A6 6 0 0012 3z" {...p} />
      </>
    ),
    // 稿件：文档
    draft: (
      <>
        <path d="M6 3h8l4 4v14a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1z" {...p} />
        <path d="M14 3v4h4M9 12h6M9 16h4" {...p} />
      </>
    ),
    // 素材工坊：图片
    assets: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" {...p} />
        <circle cx="9" cy="10" r="1.6" {...p} />
        <path d="M3.5 17l5-4.5 4 3.5 3-2.5 5 4" {...p} />
      </>
    ),
    // 发布队列：上传
    publish: (
      <>
        <path d="M12 16V4M8 8l4-4 4 4" {...p} />
        <path d="M4 15v4a1 1 0 001 1h14a1 1 0 001-1v-4" {...p} />
      </>
    ),
    // 数据看板：柱状
    analytics: (
      <>
        <path d="M4 20V9M10 20V4M16 20v-7M22 20H2" {...p} />
      </>
    ),
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
      {paths[tab] ?? paths.flow}
    </svg>
  )
}
