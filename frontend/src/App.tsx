import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ConvertResult,
  type CollectResult,
  type KernelStatus,
  type Material,
  type PipelineStatus,
  type Topic,
} from './lib/api'
import {
  DiffBadge,
  EmptyState,
  KernelDot,
  SectionTitle,
  StatCard,
  StatusBadge,
  ValueTypeBadge,
} from './components/ui'
import { AgentChat } from './components/AgentChat'
import { BREAKPOINT_NARROW, useMediaQuery } from './hooks/useMediaQuery'

type Tab = 'topics' | 'materials' | 'pipeline'

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: 'topics', label: '选题库', hint: '可执行选题' },
  { key: 'materials', label: '素材库', hint: '原始资讯' },
  { key: 'pipeline', label: '流水线', hint: '采集与转换' },
]

export default function App() {
  const [tab, setTab] = useState<Tab>('topics')
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [kernel, setKernel] = useState<KernelStatus | null>(null)
  const [topics, setTopics] = useState<Topic[]>([])
  const [materials, setMaterials] = useState<Material[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null)

  // ── Agent 侧栏状态 ──
  // 桌面端：展开时占固定宽度（380px），与主内容区并列
  // 窄屏端：降级为浮层，默认收起，避免遮挡主内容
  const isNarrow = useMediaQuery(BREAKPOINT_NARROW)
  const [agentOpen, setAgentOpen] = useState(false)

  // 窄屏时默认收起，避免一进来就被浮层挡住主内容
  useEffect(() => {
    if (isNarrow) setAgentOpen(false)
  }, [isNarrow])

  const refresh = useCallback(async () => {
    try {
      const [s, t, m] = await Promise.all([
        api.status(),
        api.topics(),
        api.materials(),
      ])
      setStatus(s)
      setTopics(t.items)
      setMaterials(m.items)
    } catch (e) {
      setToast({ kind: 'err', msg: `加载失败：${(e as Error).message}` })
    }
  }, [])

  useEffect(() => {
    refresh()
    api.kernel().then(setKernel).catch(() => setKernel({ ok: false }))
  }, [refresh])

  const notify = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg })
    setTimeout(() => setToast(null), 4000)
  }

  const runCollect = async (limit: number) => {
    setBusy('collect')
    try {
      const r: CollectResult = await api.collect(limit)
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
      const c = await api.collect(3)
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
    <div className="relative flex h-full overflow-hidden">
      {/* ── 左侧导航（窄屏隐藏，让位给主内容） ── */}
      <aside
        className={`${
          isNarrow ? 'hidden' : 'flex'
        } w-56 shrink-0 flex-col border-r border-stone-200 bg-white`}
      >
        <div className="border-b border-stone-200 px-5 py-4">
          <div className="text-sm font-semibold text-stone-900">电商工作台</div>
          <div className="mt-0.5 text-xs text-stone-400">
            小红书内容 · MVP
          </div>
        </div>

        <nav className="flex-1 p-2">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`mb-0.5 w-full rounded-lg px-3 py-2 text-left transition-colors ${
                tab === t.key
                  ? 'bg-stone-900 text-white'
                  : 'text-stone-600 hover:bg-stone-100'
              }`}
            >
              <div className="text-sm font-medium">{t.label}</div>
              <div className="text-xs text-stone-400">{t.hint}</div>
            </button>
          ))}
        </nav>

        <div className="border-t border-stone-200 px-5 py-3">
          <KernelDot online={kernel?.ok ?? false} />
          {kernel?.tools?.length ? (
            <div className="mt-1 text-xs text-stone-400">
              {kernel.tools.length} 个工具可用
            </div>
          ) : null}
        </div>
      </aside>

      {/* ── 中间主内容区（flex-1，Agent 展开也不被挤压） ── */}
      <main className="min-w-0 flex-1 overflow-y-auto">
        {/* 窄屏顶栏：显示当前页 + Agent 入口 */}
        {isNarrow && (
          <div className="sticky top-0 z-10 flex items-center justify-between border-b border-stone-200 bg-white/95 px-4 py-3 backdrop-blur">
            <div>
              <div className="text-sm font-semibold text-stone-900">
                {TABS.find((t) => t.key === tab)?.label}
              </div>
              <div className="text-xs text-stone-400">
                {TABS.find((t) => t.key === tab)?.hint}
              </div>
            </div>
            <button
              className="btn-ghost !px-2.5 !py-1.5"
              onClick={() => setAgentOpen(true)}
              aria-label="打开 Agent"
            >
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
                <path
                  d="M8 2.5C4.75 2.5 2.5 4.4 2.5 6.9c0 1.5.8 2.85 2.1 3.7l-.4 2.2 2.4-1.4c.47.08.95.12 1.4.12 3.25 0 5.5-1.9 5.5-4.62S11.25 2.5 8 2.5Z"
                  stroke="currentColor"
                  strokeWidth="1.3"
                  strokeLinejoin="round"
                />
              </svg>
              Agent
            </button>
          </div>
        )}

        <div className="mx-auto max-w-5xl px-8 py-6">
          {/* 状态概览 */}
          {status && (
            <div className="mb-6 grid grid-cols-4 gap-3">
              <StatCard label="素材" value={status.materials} />
              <StatCard label="选题" value={status.topics} tone="accent" />
              <StatCard
                label="待转换"
                value={status.unconverted_materials}
                hint={status.unconverted_materials > 0 ? '需跑转换' : '已清空'}
              />
              <StatCard label="可执行选题" value={status.available_topics} />
            </div>
          )}

          {tab === 'topics' && (
            <>
              <SectionTitle
                title="选题库"
                desc="由素材经合规过筛后转换而来，可直接进入建稿"
              />
              {topics.length === 0 ? (
                <EmptyState
                  title="选题库还是空的"
                  hint="去「流水线」页跑一次采集与转换"
                />
              ) : (
                <div className="space-y-2.5">
                  {topics.map((t) => (
                    <div
                      key={t.id}
                      className="card animate-in px-4 py-3.5 transition-colors hover:border-stone-300"
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
                        <span className="shrink-0 text-xs tabular-nums text-stone-300">
                          #{t.id}
                        </span>
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
                      className="card flex items-center gap-3 px-4 py-3"
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

                <div className="mt-6 flex flex-wrap gap-2 border-t border-stone-200 pt-4">
                  <button
                    className="btn-ghost"
                    disabled={busy !== null}
                    onClick={() => runCollect(3)}
                  >
                    {busy === 'collect' ? '采集中…' : '仅采集（每源 3 条）'}
                  </button>
                  <button
                    className="btn-ghost"
                    disabled={busy !== null}
                    onClick={() => runConvert()}
                  >
                    {busy === 'convert' ? '转换中…' : '仅转换'}
                  </button>
                  <button
                    className="btn-accent"
                    disabled={busy !== null}
                    onClick={runAll}
                  >
                    {busy === 'all' ? '执行中…' : '一键跑完整流程'}
                  </button>
                  <button
                    className="btn-ghost"
                    disabled={busy !== null}
                    onClick={refresh}
                  >
                    刷新
                  </button>
                </div>
              </div>

              {kernel && (
                <div className="card mt-4 p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-sm font-medium text-stone-900">
                      内核工具清单
                    </div>
                    <KernelDot online={kernel.ok} />
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

      {/* ── Agent 侧栏 ──
          桌面端（≥1024px）：与主内容区并列的固定列，不占用主内容宽度
          窄屏（<1024px）：降级为右侧浮层 + 遮罩，不挤压主内容
      */}
      {!isNarrow && agentOpen && (
        <aside className="w-[380px] shrink-0 animate-in border-l border-stone-200">
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
            className="fixed inset-0 z-30 bg-stone-900/30"
            onClick={() => setAgentOpen(false)}
            aria-hidden
          />
          <aside
            className="fixed inset-y-0 right-0 z-40 w-full max-w-[420px]
                       animate-in border-l border-stone-200 shadow-xl"
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
                     bg-stone-900 px-4 py-2.5 text-sm font-medium text-white
                     shadow-lg transition-colors hover:bg-stone-700"
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

      {/* ── 提示条 ──
          定位在主内容区中心：Agent 展开时不会互相遮挡
      */}
      {toast && (
        <div className="pointer-events-none fixed bottom-5 left-1/2 z-50 -translate-x-1/2">
          <div
            className={`animate-in max-w-lg rounded-lg px-4 py-2.5 text-sm shadow-lg ${
              toast.kind === 'ok'
                ? 'bg-stone-900 text-white'
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
