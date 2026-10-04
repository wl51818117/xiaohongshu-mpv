import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Tag } from './ui'

/** API 配置与模型管理（对应需求 1-5）。 */

type Model = { id: string; name: string }
type Profile = {
  id: string
  name: string
  base_url: string
  provider: string
  model: string
  api_key_masked: string
  has_key: boolean
  models: Model[]
  tested: boolean
  test_status?: string | null
  test_message?: string
  tested_at?: string | null
  test_ms?: number | null
}
type TestResult = {
  ok: boolean
  status: string
  message: string
  elapsed_ms: number
  models: Model[]
}

/** 状态 → 展示样式与中文提示（需求 2） */
const STATUS_UI: Record<
  string,
  { label: string; tone: 'green' | 'red' | 'amber' | 'gray'; hint: string }
> = {
  success: {
    label: '连接成功',
    tone: 'green',
    hint: '地址可达、密钥有效',
  },
  auth_failed: {
    label: '密钥无效或无权限',
    tone: 'red',
    hint: '检查 Key 是否正确、是否有该模型权限',
  },
  unreachable: {
    label: '地址无法访问',
    tone: 'red',
    hint: '检查 Base URL 拼写、网络与代理',
  },
  timeout: {
    label: '请求超时',
    tone: 'amber',
    hint: '网络慢或服务无响应，可重试',
  },
  bad_request: {
    label: '地址或参数不被接受',
    tone: 'amber',
    hint: '确认是 OpenAI 兼容的 Base URL',
  },
  error: {
    label: '连接异常',
    tone: 'gray',
    hint: '详见错误信息',
  },
}

export function ApiProfiles({
  onNotify,
  onChanged,
}: {
  onNotify: (kind: 'ok' | 'err', msg: string) => void
  onChanged: () => void
}) {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState({
    name: '',
    base_url: '',
    api_key: '',
    provider: '',
    model: '',
  })
  const [busy, setBusy] = useState<string>('')
  const [loadingModels, setLoadingModels] = useState('')
  const [modelsOpen, setModelsOpen] = useState<string | null>(null)
  const [modelSearch, setModelSearch] = useState('')
  const [supportsList, setSupportsList] = useState<Record<string, boolean>>({})

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/settings').then((x) => x.json())
      setProfiles(r.profiles ?? [])
      setActiveId(r.active_id ?? null)
    } catch (e) {
      onNotify('err', `读取配置失败：${(e as Error).message}`)
    }
  }, [onNotify])

  useEffect(() => {
    void load()
  }, [load])

  const resetForm = () => {
    setEditing(null)
    setForm({ name: '', base_url: '', api_key: '', provider: '', model: '' })
  }

  const startEdit = (p: Profile) => {
    setEditing(p.id)
    setForm({
      name: p.name,
      base_url: p.base_url,
      api_key: '',
      provider: p.provider,
      model: p.model,
    })
  }

  const save = async () => {
    if (!form.name.trim() || !form.base_url.trim()) {
      onNotify('err', '配置名称和地址都要填')
      return
    }
    setBusy('save')
    try {
      await fetch('/api/settings', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(editing ? { id: editing, ...form } : form),
      }).then((x) => x.json())
      onNotify('ok', editing ? '已保存配置' : '已新建配置，请点「测试连接」验证')
      resetForm()
      await load()
    } catch (e) {
      onNotify('err', `保存失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  const test = async (id: string) => {
    setBusy(`test-${id}`)
    try {
      const r: TestResult = await fetch(`/api/settings/${id}/test`, {
        method: 'POST',
      }).then((x) => x.json())
      const ui = STATUS_UI[r.status] ?? STATUS_UI.error
      onNotify(r.ok ? 'ok' : 'err', `${ui.label} · ${r.elapsed_ms}ms · ${r.message.slice(0, 60)}`)
      if (r.models?.length) setSupportsList((s) => ({ ...s, [id]: true }))
      await load()
    } catch (e) {
      onNotify('err', `测试失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  const loadModels = async (id: string) => {
    setLoadingModels(id)
    if (modelsOpen === id) {
      setModelsOpen(null)
      return
    }
    setModelsOpen(id)
    try {
      const r = await fetch(`/api/settings/${id}/models?refresh=true`).then((x) => x.json())
      setSupportsList((s) => ({ ...s, [id]: r.supported }))
      if (!r.supported && r.reason) onNotify('err', r.reason)
      else if (r.models?.length) onNotify('ok', `拉到 ${r.models.length} 个模型`)
      await load()
    } catch (e) {
      onNotify('err', `拉取模型失败：${(e as Error).message}`)
    } finally {
      setLoadingModels('')
    }
  }

  const pickModel = async (id: string, modelId: string) => {
    setBusy(`model-${id}`)
    try {
      await fetch(`/api/settings/${id}/model`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id, model: modelId }),
      }).then((x) => x.json())
      onNotify('ok', `已选择模型 ${modelId}`)
      await load()
    } catch (e) {
      onNotify('err', `设置模型失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  const activate = async (id: string, model?: string) => {
    setBusy(`active-${id}`)
    try {
      await fetch('/api/settings/active', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ profileId: id, model: model ?? '' }),
      }).then((x) => x.json())
      onNotify('ok', '已切换生效配置')
      await load()
      onChanged()
    } catch (e) {
      onNotify('err', `切换失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  const remove = async (id: string) => {
    setBusy(`del-${id}`)
    try {
      await fetch(`/api/settings/${id}`, { method: 'DELETE' }).then((x) => x.json())
      onNotify('ok', '已删除')
      await load()
    } catch (e) {
      onNotify('err', `删除失败：${(e as Error).message}`)
    } finally {
      setBusy('')
    }
  }

  const filteredModels = useMemo(() => {
    const p = profiles.find((x) => x.id === modelsOpen)
    if (!p) return []
    const kw = modelSearch.trim().toLowerCase()
    if (!kw) return p.models
    return p.models.filter(
      (m) => m.id.toLowerCase().includes(kw) || m.name.toLowerCase().includes(kw),
    )
  }, [profiles, modelsOpen, modelSearch])

  return (
    <div>
      {/* 配置表单 */}
      <div className="mb-4 rounded-xl border border-stone-200 p-3.5 dark:border-zinc-700">
        <div className="mb-2.5 flex items-center justify-between">
          <span className="text-xs font-medium text-stone-600 dark:text-stone-300">
            {editing ? '编辑配置' : '新建配置'}
          </span>
          {editing && (
            <Button variant="ghost" size="xs" onClick={resetForm}>
              取消
            </Button>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="配置名称，如 DeepSeek官方"
              className="field !min-h-[32px] !py-1.5 !text-xs"
            />
            <input
              value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
              placeholder="提供商（可选）"
              className="field !min-h-[32px] !w-28 !py-1.5 !text-xs"
            />
          </div>
          <input
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="Base URL，如 https://api.deepseek.com"
            className="field !min-h-[32px] !py-1.5 !font-mono !text-[11px]"
          />
          <div className="flex gap-2">
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder={
                editing && form.model
                  ? '留空则不修改当前 Key'
                  : 'API 密钥（加密存储）'
              }
              autoComplete="off"
              className="field !min-h-[32px] !flex-1 !py-1.5 !font-mono !text-[11px]"
            />
            <Button
              variant="primary"
              size="sm"
              loading={busy === 'save'}
              onClick={() => void save()}
            >
              {editing ? '保存修改' : '添加配置'}
            </Button>
          </div>
        </div>

        <p className="mt-2 text-[11px] leading-relaxed text-stone-400">
          密钥经机器密钥派生加密后存于本机
          <code>backend/data/secrets.json</code>，
          <strong>不以明文落盘、不进版本库</strong>；列表只显示掩码。
        </p>
      </div>

      {/* 配置列表 */}
      {profiles.length === 0 ? (
        <p className="rounded-xl border border-dashed border-stone-300 py-10 text-center text-sm text-stone-400">
          还没有 API 配置 —— 上面新建一个
        </p>
      ) : (
        <div className="space-y-2.5">
          {profiles.map((p) => {
            const ui = STATUS_UI[p.test_status ?? ''] ?? null
            const isActive = activeId === p.id
            const sel = supportsList[p.id]
            return (
              <div
                key={p.id}
                className={`rounded-xl border p-3 transition-colors ${
                  isActive
                    ? 'border-orange-300 bg-orange-50/50 dark:border-orange-700 dark:bg-orange-500/10'
                    : 'border-stone-200 dark:border-zinc-700'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm font-medium text-stone-800 dark:text-stone-100">
                        {p.name}
                      </span>
                      {isActive && <Tag tone="orange">生效中</Tag>}
                      {p.tested ? (
                        <Tag tone="green">已测试</Tag>
                      ) : (
                        <Tag tone="gray">未测试</Tag>
                      )}
                      {ui && <Tag tone={ui.tone}>{ui.label}</Tag>}
                    </div>
                    <div className="mt-1 truncate font-mono text-[11px] text-stone-400">
                      {p.base_url}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px] text-stone-500 dark:text-stone-400">
                      <span>
                        Key：
                        <span className="text-stone-600 dark:text-stone-300">
                          {p.api_key_masked || '未填'}
                        </span>
                      </span>
                      <span>
                        模型：
                        <span className="text-stone-600 dark:text-stone-300">
                          {p.model || '未选'}
                        </span>
                      </span>
                      {p.test_ms != null && <span>{p.test_ms}ms</span>}
                    </div>
                    {p.test_message && (
                      <p className="mt-1 text-[11px] leading-relaxed text-stone-400">
                        {p.test_message}
                        {ui ? ` — ${ui.hint}` : ''}
                      </p>
                    )}
                  </div>

                  {/* 操作 */}
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    <div className="flex gap-1">
                      <Button
                        variant="default"
                        size="xs"
                        loading={busy === `test-${p.id}`}
                        onClick={() => void test(p.id)}
                      >
                        测试连接
                      </Button>
                      <Button
                        variant="default"
                        size="xs"
                        loading={loadingModels === p.id}
                        onClick={() => void loadModels(p.id)}
                      >
                        {modelsOpen === p.id ? '收起' : '模型列表'}
                      </Button>
                    </div>
                    <div className="flex gap-1">
                      <Button
                        variant="default"
                        size="xs"
                        onClick={() => startEdit(p)}
                      >
                        编辑
                      </Button>
                      {!isActive && (
                        <Button
                          variant="primary"
                          size="xs"
                          loading={busy === `active-${p.id}`}
                          disabled={!p.tested}
                          title={
                            p.tested
                              ? '设为生效配置'
                              : '需先通过连接测试'
                          }
                          onClick={() => void activate(p.id)}
                        >
                          设为生效
                        </Button>
                      )}
                      <Button
                        variant="danger"
                        size="xs"
                        loading={busy === `del-${p.id}`}
                        onClick={() => void remove(p.id)}
                      >
                        删
                      </Button>
                    </div>
                  </div>
                </div>

                {/* 模型下拉（需求 3/4） */}
                {modelsOpen === p.id && (
                  <div className="mt-2.5 rounded-lg bg-stone-50 p-2.5 dark:bg-zinc-800/60">
                    {sel === false ? (
                      <div className="space-y-1.5">
                        <p className="text-[11px] text-amber-700 dark:text-amber-300">
                          该服务不支持模型列表接口 —— 请手动输入模型名
                        </p>
                        <div className="flex gap-1.5">
                          <input
                            defaultValue={p.model}
                            placeholder="如 deepseek-chat"
                            className="field !min-h-[28px] !flex-1 !py-1 !font-mono !text-[11px]"
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                const v = (e.target as HTMLInputElement).value.trim()
                                if (v) void pickModel(p.id, v)
                              }
                            }}
                          />
                          <Button
                            variant="primary"
                            size="xs"
                            onClick={() => {
                              const el = document.querySelector<HTMLInputElement>(
                                `input[data-profile="${p.id}"]`,
                              )
                              if (el?.value.trim()) void pickModel(p.id, el.value.trim())
                            }}
                            data-save-for={p.id}
                          >
                            保存
                          </Button>
                        </div>
                        <p className="text-[10px] text-stone-400">
                          在上方输入框回车即可保存
                        </p>
                      </div>
                    ) : p.models.length === 0 ? (
                      <p className="text-[11px] text-stone-400">正在拉取…</p>
                    ) : (
                      <>
                        <input
                          value={modelSearch}
                          onChange={(e) => setModelSearch(e.target.value)}
                          placeholder="搜索模型…"
                          className="field mb-1.5 !min-h-[28px] !py-1 !text-[11px]"
                        />
                        <div className="max-h-40 overflow-y-auto">
                          {filteredModels.map((m) => (
                            <button
                              key={m.id}
                              onClick={() => void pickModel(p.id, m.id)}
                              className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[11px] transition-colors ${
                                p.model === m.id
                                  ? 'bg-orange-100 font-medium text-orange-800 dark:bg-orange-500/20 dark:text-orange-200'
                                  : 'text-stone-600 hover:bg-stone-200 dark:text-stone-300 dark:hover:bg-zinc-700'
                              }`}
                            >
                              <span className="font-mono">{m.id}</span>
                              {m.name !== m.id && (
                                <span className="truncate text-stone-400">
                                  {m.name}
                                </span>
                              )}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
