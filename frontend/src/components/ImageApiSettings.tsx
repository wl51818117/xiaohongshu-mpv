import { useCallback, useEffect, useState } from 'react'
import { Button, SectionTitle, Tag } from './ui'

/** 生图 API 配置。
 *
 * 与 LLM 配置分开管理：两者协议、计费、密钥都不同，混在一起必然混乱。
 * 密钥复用 settings 的 Fernet 加密（由机器信息派生密钥），查询只返掩码。
 */

type ProviderStatus = {
  configured: boolean
  enabled: boolean
  kind: string
  base_url: string
  model: string
  size: string
  key_masked: string
  hint: string
}

type TestState = {
  status: 'idle' | 'testing' | 'success' | 'auth_failed' | 'unreachable' | 'bad_request' | 'error'
  message: string
  ms?: number
}

const STATUS_UI: Record<string, { label: string; tone: 'green' | 'red' | 'amber' | 'gray' }> = {
  success: { label: '连接成功', tone: 'green' },
  auth_failed: { label: '密钥无效或无权限', tone: 'red' },
  unreachable: { label: '地址无法访问', tone: 'red' },
  bad_request: { label: '地址或模型不对', tone: 'amber' },
  error: { label: '调用失败', tone: 'red' },
}

const KIND_LABEL: Record<string, string> = {
  openai: 'OpenAI 兼容（DALL·E / SiliconFlow / 多数聚合服务）',
  seedream: 'Seedream（即梦）',
}

export function ImageApiSettings({ onNotify }: { onNotify: (k: 'ok' | 'err', m: string) => void }) {
  const [st, setSt] = useState<ProviderStatus | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [kind, setKind] = useState('openai')
  const [size, setSize] = useState('1080x1440')
  const [enabled, setEnabled] = useState(false)
  const [test, setTest] = useState<TestState>({ status: 'idle', message: '' })
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/assets/provider')
      const d = await r.json()
      setSt(d)
      setBaseUrl(d.base_url || '')
      setModel(d.model || '')
      setKind(d.kind || 'openai')
      setSize(d.size || '1080x1440')
      setEnabled(Boolean(d.enabled))
      // 密钥不回显，留空表示沿用已保存的
      setApiKey('')
    } catch (e) {
      onNotify('err', `读取生图配置失败：${(e as Error).message}`)
    }
  }, [onNotify])

  useEffect(() => {
    void load()
  }, [load])

  const runTest = async () => {
    if (!baseUrl.trim()) {
      setTest({ status: 'error', message: '请先填写服务地址' })
      return
    }
    if (!apiKey.trim() && !st?.configured) {
      setTest({ status: 'error', message: '请先填写 API 密钥' })
      return
    }
    setTest({ status: 'testing', message: '测试中…' })
    try {
      const r = await fetch('/api/assets/provider/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, model, kind, size }),
      })
      const d = await r.json()
      setTest({ status: d.status, message: d.message, ms: d.ms })
    } catch (e) {
      setTest({ status: 'error', message: (e as Error).message })
    }
  }

  const save = async () => {
    // 前端也做一次校验，别把明显不完整的配置发出去
    if (enabled) {
      const missing: string[] = []
      if (!baseUrl.trim()) missing.push('地址')
      if (!apiKey.trim() && !st?.configured) missing.push('密钥')
      if (!model.trim()) missing.push('模型')
      if (missing.length) {
        onNotify('err', `启用前必须填写：${missing.join('、')}`)
        return
      }
    }
    setSaving(true)
    try {
      const r = await fetch('/api/assets/provider', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          base_url: baseUrl,
          api_key: apiKey,
          model,
          kind,
          size,
          enabled,
        }),
      })
      const d = await r.json()
      if (!d.ok) {
        onNotify('err', d.detail || '保存失败')
        return
      }
      onNotify('ok', enabled ? '已启用真实生图' : '已保存（未启用，继续用占位图）')
      setApiKey('')
      await load()
    } catch (e) {
      onNotify('err', `保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const ui = test.status !== 'idle' && test.status !== 'testing' ? STATUS_UI[test.status] : null

  return (
    <div>
      <SectionTitle
        title="生图 API"
        desc="素材工坊的真实出图服务。与 LLM 配置分开管理，密钥同样本地加密存储。"
      />

      {/* 当前状态 */}
      <div
        className={`mb-4 rounded-[var(--r-md)] border px-3 py-2.5 text-[12px] ${
          st?.configured
            ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300'
            : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300'
        }`}
      >
        {st?.configured ? (
          <>
            已接入 <strong className="font-medium">{st.model}</strong>
            {st.key_masked && <span className="ml-1 opacity-80">（{st.key_masked}）</span>}
          </>
        ) : (
          <>
            <strong className="font-medium">当前使用本地占位图</strong>
            <span className="ml-1">——尺寸真实、画面为占位。填写下方配置并启用后自动切换为真实生图。</span>
          </>
        )}
      </div>

      <div className="card p-5">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="field-label">服务地址（Base URL）</label>
            <input
              className="field"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.siliconflow.cn/v1"
            />
            <p className="mt-1 text-[11px] text-stone-400">
              需含协议头与版本路径（如 /v1）。系统会在其后拼接 /images/generations。
            </p>
          </div>

          <div>
            <label className="field-label">API 密钥</label>
            <input
              className="field"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={st?.key_masked || '粘贴生图服务的密钥'}
            />
            <p className="mt-1 text-[11px] text-stone-400">
              {st?.key_masked
                ? '已保存密钥，留空表示沿用原密钥。'
                : '本地加密存储，不上传、不入库。'}
            </p>
          </div>

          <div>
            <label className="field-label">模型名</label>
            <input
              className="field"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="black-forest-labs/FLUX.1-schnell"
            />
          </div>

          <div>
            <label className="field-label">协议类型</label>
            <select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(KIND_LABEL).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="field-label">出图尺寸</label>
            <input
              className="field"
              value={size}
              onChange={(e) => setSize(e.target.value)}
              placeholder="1080x1440"
            />
            <p className="mt-1 text-[11px] text-stone-400">
              平台硬要求 3:4竖版；非 3:4 会自动加白边纠正（不拉伸，避免商品变形）。
            </p>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between border-t border-stone-200 pt-4">
          <label className="flex cursor-pointer items-center gap-2 text-[13px]">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            启用真实生图（关闭则继续用本地占位图）
          </label>

          <div className="flex gap-2">
            <Button variant="default" size="sm" loading={test.status === 'testing'} onClick={runTest}>
              测试连接
            </Button>
            <Button variant="primary" size="sm" loading={saving} onClick={save}>
              保存
            </Button>
          </div>
        </div>

        {/* 测试结果 */}
        {ui && (
          <div className="mt-3 flex items-center gap-2 text-[12px]">
            <Tag tone={ui.tone}>{ui.label}</Tag>
            <span className="text-stone-600 dark:text-stone-300">{test.message}</span>
            {test.ms !== undefined && (
              <span className="text-stone-400">耗时 {test.ms}ms</span>
            )}
          </div>
        )}
      </div>

      <p className="mt-3 text-[11px] text-stone-400">
        提示：多数服务需先在平台充值开通生图权限；测试连接会真实发起一次最小出图请求（可能计费极少量）。
      </p>
    </div>
  )
}
