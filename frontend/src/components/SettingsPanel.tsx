import { useCallback, useEffect, useState } from 'react'
import { SectionTitle, Tag } from './ui'
import { FeedsManager } from './FeedsManager'

type SettingsData = {
  kernel: {
    base_url: string
    provider: string
    model: string
    api_key_masked: string
    has_key: boolean
  }
  prefs: Record<string, unknown>
}

type TestResult = {
  reachable: boolean
  authenticated: boolean
  model?: { provider?: string; model?: string }
  apps?: { apps?: number; capabilities?: number }
  tools_count?: number
  error?: string
}

/** 设置抽屉：内核 API Key、模型、连接测试。 */
export function SettingsPanel({
  open,
  onClose,
  onNotify,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onSaved: () => void
}) {
  const [data, setData] = useState<SettingsData | null>(null)
  const [form, setForm] = useState({
    base_url: 'http://127.0.0.1:8787',
    api_key: '',
    model: '',
    provider: 'deepseek-official',
  })
  const [test, setTest] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  // 设置面板的标签页：内核 / RSS 源
  const [tab, setTab] = useState<'kernel' | 'feeds'>('kernel')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/settings').then((x) => x.json())
      setData(r)
      setForm((f) => ({
        ...f,
        base_url: r.kernel.base_url,
        model: r.kernel.model,
        provider: r.kernel.provider,
        // api_key 故意不回填 —— 后端只给掩码
      }))
    } catch {
      /* 设置读取失败不阻塞主界面 */
    }
  }, [])

  useEffect(() => {
    if (open) {
      void load()
      void runTest()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const runTest = useCallback(async () => {
    setBusy('test')
    try {
      const r = await fetch('/api/settings/kernel/test', { method: 'POST' }).then((x) =>
        x.json(),
      )
      setTest(r)
    } catch (e) {
      setTest({ reachable: false, authenticated: false, error: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }, [])

  const save = async () => {
    setBusy('save')
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          kernel: {
            base_url: form.base_url,
            api_key: form.api_key,
            model: form.model,
            provider: form.provider,
          },
        }),
      }).then((x) => x.json())
      if (r.ok) {
        onNotify('ok', '已保存（API Key 仅存本地，不入库）')
        setForm((f) => ({ ...f, api_key: '' })) // 清空输入框
        await load()
        onSaved()
      } else {
        onNotify('err', '保存失败')
      }
    } catch (e) {
      onNotify('err', `保存失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  if (!open) return null

  return (
    <>
      {/* 遮罩 */}
      <div
        className="fixed inset-0 z-40 bg-stone-900/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* 抽屉 */}
      <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md animate-in flex-col border-l border-[var(--border)] bg-white shadow-2xl dark:bg-zinc-900">
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div>
            <div className="text-sm font-semibold text-stone-900 dark:text-stone-50">
              设置
            </div>
            <div className="mt-0.5 text-[11px] text-stone-400">
              内核连接与模型配置
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="关闭设置"
            className="rounded-lg p-1.5 text-stone-400 transition-colors hover:bg-stone-100 dark:hover:bg-zinc-800"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* 标签页 */}
        <div className="flex shrink-0 gap-1 border-b border-[var(--border)] px-5">
          {(
            [
              ['kernel', '内核与模型'],
              ['feeds', 'RSS 源'],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${
                tab === k
                  ? 'border-orange-700 text-orange-700 dark:border-orange-400 dark:text-orange-400'
                  : 'border-transparent text-stone-500 hover:text-stone-700'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 内容 */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {tab === 'feeds' ? (
            <FeedsManager onNotify={onNotify} />
          ) : (
            <>
          {/* 连接状态 */}
          {/* 连接状态 */}
          <div className="rounded-xl bg-zinc-50 p-3.5 dark:bg-zinc-800/60">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-stone-600 dark:text-stone-300">
                连接状态
              </span>
              <button
                onClick={() => void runTest()}
                disabled={busy !== null}
                className="btn btn-ghost btn-sm"
              >
                {busy === 'test' ? '检测中…' : '重新检测'}
              </button>
            </div>
            <div className="space-y-1.5 text-xs">
              <Row
                label="服务可达"
                ok={test?.reachable}
                pending={!test}
              />
              <Row
                label="鉴权通过"
                ok={test?.authenticated}
                pending={!test}
              />
              {test?.reachable && test.model && (
                <div className="text-stone-500 dark:text-stone-400">
                  当前模型：
                  <span className="text-stone-700 dark:text-stone-200">
                    {test.model.provider} / {test.model.model}
                  </span>
                </div>
              )}
              {test?.authenticated && (
                <div className="text-stone-500 dark:text-stone-400">
                  可用工具：<span className="text-stone-700">{test.tools_count}</span>
                </div>
              )}
              {test?.apps && (
                <div className="text-stone-500 dark:text-stone-400">
                  已接应用：
                  <span className="text-stone-700">{test.apps.apps ?? 0}</span>，
                  能力：
                  <span className="text-stone-700">{test.apps.capabilities ?? 0}</span>
                </div>
              )}
              {test?.error && (
                <div className="break-all text-red-600">{test.error}</div>
              )}
            </div>
          </div>

          {/* 内核配置 */}
          <div>
            <SectionTitle title="内核配置" desc="改完需重启内核生效" />

            <div className="space-y-3">
              <div>
                <label className="field-label">内核地址</label>
                <input
                  value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  className="field font-mono !text-xs"
                  placeholder="http://127.0.0.1:8787"
                />
              </div>

              <div>
                <label className="field-label">
                  DeepSeek API Key
                  {data?.kernel.has_key && (
                    <span className="ml-2 font-normal text-emerald-600">
                      已配置
                    </span>
                  )}
                </label>
                <input
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  className="field font-mono !text-xs"
                  placeholder={
                    data?.kernel.has_key
                      ? '留空则不修改（当前：' +
                        data.kernel.api_key_masked +
                        '）'
                      : 'sk-xxxxxxxxxxxxxxxx'
                  }
                  autoComplete="off"
                />
                <p className="mt-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                  <strong>安全说明</strong>：Key 仅写入本机
                  <code> backend/data/secrets.json</code>，已在 .gitignore 中排除，
                  <strong>不会进版本库</strong>；查询时只返回掩码，永不回显明文。
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="field-label">提供商</label>
                  <select
                    value={form.provider}
                    onChange={(e) => setForm({ ...form, provider: e.target.value })}
                    className="field !text-xs"
                  >
                    <option value="deepseek-official">DeepSeek 官方</option>
                    <option value="custom">自定义网关</option>
                  </select>
                </div>
                <div>
                  <label className="field-label">模型</label>
                  <input
                    value={form.model}
                    onChange={(e) => setForm({ ...form, model: e.target.value })}
                    className="field font-mono !text-xs"
                    placeholder="deepseek-flash"
                  />
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-[var(--border)] p-3.5 text-[11px] leading-relaxed text-stone-500 dark:text-stone-400">
            <div className="mb-1.5 font-medium text-stone-600 dark:text-stone-300">
              怎么填
            </div>
            1. 内核地址默认 <code>http://127.0.0.1:8787</code>，一般不用改
            <br />
            2. API Key 填 DeepSeek 平台的 Key（sk- 开头）
            <br />
            3. 模型留空则用内核默认（deepseek-flash）
            <br />
            4. 保存后需<strong>重启内核</strong>（
            <code>提取harness/start-all.cmd</code>）才会生效
          </div>
            </>
          )}
        </div>

        {/* 底部操作 */}
        <div className="flex shrink-0 gap-2 border-t border-[var(--border)] px-5 py-3">
          <button
            className="btn btn-primary flex-1"
            disabled={busy !== null}
            onClick={() => void save()}
          >
            {busy === 'save' ? '保存中…' : '保存设置'}
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </>
  )
}

/** 状态行 */
function Row({
  label,
  ok,
  pending,
}: {
  label: string
  ok?: boolean
  pending?: boolean
}) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          pending ? 'bg-stone-300' : ok ? 'bg-emerald-500' : 'bg-red-500'
        }`}
      />
      <span className="text-stone-600 dark:text-stone-300">{label}</span>
      <span className="ml-auto">
        {pending ? (
          <Tag tone="gray">检测中</Tag>
        ) : ok ? (
          <Tag tone="green">正常</Tag>
        ) : (
          <Tag tone="rose">异常</Tag>
        )}
      </span>
    </div>
  )
}
