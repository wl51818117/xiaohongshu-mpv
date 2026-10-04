/**
 * harness-bridge.js 的类型声明。
 *
 * SDK 是零依赖的单文件 UMD，没有自带 .d.ts。
 * 这里按我们实际用到的 API 手写最小类型集，够用且不撒谎。
 */

export interface CapabilityOutput {
  schema: Record<string, unknown>
}

export type CapabilityHandler = (args: any) => Promise<unknown>

export interface CapabilityDef {
  name: string
  title?: string
  description: string
  parameters: Record<string, unknown>
  output?: CapabilityOutput
  annotations?: Record<string, boolean>
  signal?: AbortSignal
  handler: CapabilityHandler
}

export type TokenResult = string | { token: string; expiresAt?: number }

export interface ConnectOptions {
  baseUrl: string
  appId: string
  capabilities: CapabilityDef[]
  /**
   * 短时票据来源。
   * 返回字符串，或返回 { token, expiresAt } 让 SDK 自己缓存并按时续签
   * （expiresAt 支持毫秒或秒级时间戳）。
   */
  tokenProvider?: () => Promise<TokenResult>
  fetch?: typeof fetch
  clientId?: string
}

export interface BridgeEvent {
  type: string
  data?: unknown
}

export interface Bridge {
  chat(text: string, opts?: Record<string, unknown>): AsyncIterable<BridgeEvent>
  resume(opts?: Record<string, unknown>): AsyncIterable<BridgeEvent>
  register(caps: CapabilityDef[]): Promise<void>
  setContext(ctx: Record<string, unknown>): void
  patchContext(patch: Record<string, unknown>): void
  whenSynced(): Promise<void>
  on(event: string, cb: (data: unknown) => void): void
  close?(): void
}

export interface HarnessBridgeApi {
  defineCapability(def: CapabilityDef): CapabilityDef
  connect(opts: ConnectOptions): Promise<Bridge>
  fromForm(form: unknown): CapabilityDef
  createClient(opts: Record<string, unknown>): Record<string, unknown>
  mount(target: string | HTMLElement, opts: Record<string, unknown>): unknown
}

declare global {
  interface Window {
    HarnessBridge?: HarnessBridgeApi
  }
}

export declare const HarnessBridge: HarnessBridgeApi
