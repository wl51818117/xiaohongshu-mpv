/**
 * HarnessBridge v2.0 —— 通用嵌入层
 * ===========================================================================
 * 一句话：**让你的 Web 应用变成 agent 能看懂、能操作的接口。**
 *
 * v1（harness-chat.js）解决的是「给应用加一个对话框」——单向的：用户说话，
 * 内核思考，内核回答。应用的业务逻辑在内核那边（服务端注册工具）。
 *
 * v2（本文件）解决的是「让应用自己成为 agent 的手和眼」——双向的：
 *   应用声明自己有哪些操作（能力清单 manifest）
 *     → 内核把这些操作动态注册成模型可调用的工具
 *     → 模型决定调用时，内核把调用**反向推回浏览器**
 *     → 应用在**自己的 JS 上下文里**执行（拿得到 store、路由、登录态、DOM）
 *     → 结果回填内核，模型继续推理
 *
 * 这就是「植入任意 Web 端应用」的关键：业务逻辑本来就在应用里，不用搬到服务端。
 *
 * ---------------------------------------------------------------------------
 * 三种用法
 *
 *   1) 只要能力，界面自己写（最常用）：
 *        const bridge = await HarnessBridge.connect({
 *          baseUrl: 'http://127.0.0.1:8787',
 *          appId: 'shop-admin',
 *          capabilities: [
 *            HarnessBridge.defineCapability({
 *              name: 'query_order',
 *              description: '按订单号查询订单状态。用户问某笔订单的物流/状态时使用。',
 *              parameters: { type:'object', properties:{ orderId:{type:'string'} }, required:['orderId'] },
 *              handler: async ({ orderId }) => myStore.getOrder(orderId),
 *            }),
 *          ],
 *        })
 *        for await (const evt of bridge.chat('帮我查订单 A123')) { ... }
 *
 *   2) 要现成的对话窗口：
 *        HarnessBridge.mount('#chat', { baseUrl: '...', appId: '...', capabilities: [...] })
 *
 *   3) 只想上报能力、不要对话（agent 从别处被驱动）：
 *        await HarnessBridge.connect({ ..., chat: false })
 *
 * ---------------------------------------------------------------------------
 * 设计约束（刻意为之）
 *   - 零依赖、单文件、UMD：<script> / ESM / CJS / Web Worker 都能用
 *   - 无 DOM 依赖的核心部分（connect 及其返回对象）可跑在 Node / Worker / RN
 *   - 能力 handler 是**本地函数**，绝不序列化上传；上行只有 JSON Schema 描述
 *   - 断线不静默失败：所有失败都通过 error 事件暴露，绝不吞掉
 */

;(function (global, factory) {
  if (typeof module === 'object' && typeof module.exports === 'object') {
    module.exports = factory()
  } else if (typeof define === 'function' && define.amd) {
    define([], factory)
  } else {
    global.HarnessBridge = factory()
  }
})(typeof globalThis !== 'undefined' ? globalThis : typeof self !== 'undefined' ? self : this, function () {
  'use strict'

  var VERSION = '2.1.0'
  var PROTOCOL = 'hbridge/2'
  /** 仍可对接的协议版本（宿主侧做协商，不在列表里会被 400）。 */
  var COMPATIBLE_PROTOCOLS = ['hbridge/1', 'hbridge/2']

  /* ======================================================================
   * 一、基础工具（与 v1 保持一致的实现，避免行为漂移）
   * ==================================================================== */

  function trimSlash(s) {
    return String(s || '').replace(/\/+$/, '')
  }

  function uid(prefix) {
    var rand = Math.random().toString(36).slice(2, 10)
    var time = Date.now().toString(36).slice(-5)
    return (prefix || 'id') + '-' + time + rand
  }

  function safeJson(text) {
    try {
      return JSON.parse(text)
    } catch (e) {
      return null
    }
  }

  /** 把一个 SSE 帧（"id: 12\nevent: x\ndata: {...}"）解析成 { id, type, data }。 */
  function parseFrame(raw) {
    if (!raw || !raw.trim()) return null
    var type = 'message'
    var id = 0
    var dataLines = []
    var lines = raw.split('\n')
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i]
      if (line.indexOf('event:') === 0) type = line.slice(6).trim()
      else if (line.indexOf('id:') === 0) id = Number(line.slice(3).trim()) || 0
      else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim())
    }
    var joined = dataLines.join('\n')
    return { id: id, type: type, data: safeJson(joined) || { raw: joined } }
  }

  /** 从 assistant/message 或 tool/result 的 message.content 里抽出纯文本。 */
  function textOfMessage(message) {
    if (!message) return ''
    if (typeof message === 'string') return message
    var content = message.content
    if (typeof content === 'string') return content
    if (!Array.isArray(content)) return ''
    var out = ''
    for (var i = 0; i < content.length; i++) {
      var block = content[i]
      if (block && block.type === 'text' && typeof block.text === 'string') out += block.text
    }
    return out
  }

  /* ======================================================================
   * 二、事件归一化 —— 抹平内核的两种事件信封
   * ----------------------------------------------------------------------
   * 内核历史上把负载分两处放：
   *   - text-delta / reasoning-delta / approval-request：负载在**顶层**
   *   - turn/step/tool/assistant：负载在 data.data 里（会话事件信封）
   * 上层如果不知道这个区别，渲染必然出错（v1 就踩过这个坑）。
   * 这里统一成 { type, data, envelope }，调用方只读 data。
   * ==================================================================== */

  var TOP_LEVEL_EVENTS = {
    'session': 1,
    'text-delta': 1,
    'reasoning-delta': 1,
    'tool-invoke': 1,
    'tool-cancel': 1,
    'tool-progress': 1,
    'state-delta': 1,
    'approval-request': 1,
    'done': 1,
    'error': 1,
  }

  function normalizeEvent(frame) {
    var type = frame.type
    var payload = frame.data || {}
    if (TOP_LEVEL_EVENTS[type]) return { type: type, data: payload, envelope: payload }
    // 会话事件信封：真实负载在 data.data
    return { type: type, data: payload.data || payload, envelope: payload }
  }

  /* ======================================================================
   * 三、能力（capability）定义与校验
   * ==================================================================== */

  var SIDE_EFFECTS = ['read', 'write', 'destructive']
  var APPROVAL_MODES = ['auto', 'always', 'never']

  /**
   * 能力名规则。
   *
   * 对齐 MCP / WebMCP 的 `[A-Za-z0-9_.-]{1,128}`，但**加两条本协议的额外限制**：
   *   1. 首字符必须是字母或下划线（内核的工具名习惯，也避免出现纯数字名）；
   *   2. `.` 和 `-` 会被**规范化成 `_`** 再上报。
   *
   * 为什么第 2 条：内核 `ctx.tools.register(name)` 对名字的容忍度无法在本环境验证，
   * 而含 `.` / `-` 的名字在多数工具注册表里是边缘情况。**规范化比赌一把安全**——
   * 应用可以按 MCP 习惯书写（`orders.get`），上报出去的名字一定内核安全。
   * 规范化后撞名的，由内核的撞名降级规则兜住（加 `<appId>__` 前缀）。
   */
  var NAME_RE = /^[A-Za-z_][A-Za-z0-9_.-]{0,127}$/

  /** `orders.get` / `orders-get` → `orders_get`（内核安全）。 */
  function normalizeName(name) {
    return String(name || '').replace(/[.-]/g, '_')
  }

  /**
   * 上下文里绝不允许出现的键。
   *
   * 用 `obj[k] = v` 写入 `__proto__` 走的是 setter，会把目标对象的**原型**
   * 整个换掉（不是普通赋值）。前端这边目标对象就是 SDK 持有的 context，
   * 一旦被换掉，后续 `for...in` / 继承属性查找都会读到意外内容。
   * 与内核 `session-store.js` 的 BLOCKED_CONTEXT_KEYS 保持一致。
   *
   * 注意：这里用数组而不是 `{ __proto__: 1 }` —— 对象字面量里的 `__proto__:`
   * 是「设原型」语法，根本不会建出这个键，写成对象反而是个坑。
   */
  var BLOCKED_CONTEXT_KEYS = ['__proto__', 'constructor', 'prototype']

  /** 键是否允许写入上下文。 */
  function contextKeyAllowed(key) {
    return BLOCKED_CONTEXT_KEYS.indexOf(key) === -1
  }

  /**
   * 把 `patch` 合并进 `target`（原地），跳过原型相关键，返回真正生效的键。
   * 用 Object.entries 而不是 for...in —— 后者会把**继承来的**可枚举属性
   * 也一起复制过来。
   */
  function mergeOwn(target, patch) {
    var applied = []
    if (!patch || typeof patch !== 'object') return applied
    var entries = Object.entries(patch)
    for (var i = 0; i < entries.length; i++) {
      var k = entries[i][0]
      var v = entries[i][1]
      if (!contextKeyAllowed(k)) continue
      if (v === null || v === undefined) {
        if (Object.prototype.hasOwnProperty.call(target, k)) delete target[k]
      } else {
        target[k] = v
      }
      applied.push(k)
    }
    return applied
  }

  /**
   * 浅拷一份**只含允许键**的上下文对象。
   *
   * 与 `mergeOwn` 的区别：这里保留 `null`（全量替换时 null 是有意义的取值），
   * 只做「键白名单」，不带删除语义。
   *
   * 为什么需要它：`JSON.parse('{"__proto__":{...}}')` 走 CreateDataProperty，
   * 会**真的建出** `__proto__` 自有键（对象字面量 `{ __proto__: x }` 反而是
   * 「设原型」语法，建不出这个键）。若不过滤，`getContext()` 返回的对象就带着
   * 一个 `__proto__` 自有键，任何消费方只要写 `Object.assign({}, ctx)`，
   * 就会触发 `__proto__` setter 把目标对象的原型换掉。
   */
  function sanitizeContext(src) {
    var out = {}
    if (src && typeof src === 'object') {
      var entries = Object.entries(src)
      for (var i = 0; i < entries.length; i++) {
        var k = entries[i][0]
        if (!contextKeyAllowed(k)) continue
        out[k] = entries[i][1]
      }
    }
    return out
  }

  /**
   * MCP / WebMCP 的注解体系（6 个 hint）。
   * `sideEffects` 保留为向后兼容的快捷写法，两者同时给出时 **annotations 优先**。
   */
  var ANNOTATION_HINTS = [
    'readOnlyHint',
    'destructiveHint',
    'idempotentHint',
    'openWorldHint',
    'untrustedContentHint',
    'consequentialHint',
  ]

  /**
   * 推导注解。默认值**有意与 MCP 的默认值不同**，理由写在每一项上：
   *
   * - `readOnlyHint`      默认由 sideEffects 推（read → true）
   * - `destructiveHint`   默认由 sideEffects 推（destructive → true）
   *                          ⚠️ MCP 默认 **true**（保守）；我们按声明推，但**宿主仍会用自己的
   *                          策略表重新判定**，应用自报只作提示（见 kernel/host/bridge.js）
   * - `idempotentHint`    默认 **false**（MCP 同为 false）。重放安全必须显式声明——
   *                          这正是断线续传「重放的 tool-invoke 至少一次」要用的信号
   * - `openWorldHint`     默认 **false**，⚠️ 与 MCP 默认（true）相反。
   *                          理由：客户端能力的操作域就是**该应用自己**（改自己的 store、
   *                          点自己的 DOM），不是「与外部世界交互」。真去调外部 API 的能力
   *                          应当显式写 `openWorldHint: true`
   * - `untrustedContentHint` 默认 **false**。返回值若含用户生成内容 / 外部抓来的文本
   *                          （评论、工单描述、网页正文），应显式置 true —— 这是
   *                          prompt injection 的第一道标记
   * - `consequentialHint` 默认由 sideEffects + approval 推（写操作或需审批 → true）
   */
  function deriveAnnotations(cap, sideEffects, approval) {
    var given = (cap && cap.annotations) || {}
    var readOnly = sideEffects === 'read'
    var destructive = sideEffects === 'destructive'
    var defaults = {
      readOnlyHint: readOnly,
      destructiveHint: destructive,
      idempotentHint: false,
      openWorldHint: false,
      untrustedContentHint: false,
      consequentialHint: !readOnly || approval === 'always',
    }
    var out = {}
    for (var i = 0; i < ANNOTATION_HINTS.length; i++) {
      var k = ANNOTATION_HINTS[i]
      out[k] = given[k] === undefined ? defaults[k] : Boolean(given[k])
    }
    return out
  }

  /**
   * 声明一个能力。这是应用侧唯一需要理解的形状。
   *
   * @param {object} cap
   * @param {string}   cap.name          工具名。建议 <领域>_<动作>（如 query_order）；
   *                                     也接受 MCP 风格（`orders.get`，会被规范化为 orders_get）
   * @param {string=}  cap.title         人类可读展示名（给本地化 UI 用）；默认取规范化前的原名
   * @param {string}   cap.description   ★ 给模型看的说明书，写清「什么时候该用它」
   * @param {object=}  cap.parameters    入参 JSON Schema（别名 `inputSchema` 等价）
   * @param {object=}  cap.output        出参 { schema?（别名 outputSchema）, format?, maxChars?, structured?, render? }
   *                                      format='text' 时 value 直接当文本喂给模型（省 token）
   *                                      format='blocks' 时 value 是 content block 数组
   *                                      structured=true 时额外把原始 JSON 附在渲染结果后面
   * @param {string=}  cap.sideEffects   read | write | destructive（默认 read）——注解的快捷写法
   * @param {string=}  cap.approval      auto | always | never（默认 auto）
   * @param {object=}  cap.annotations   MCP 风格 6 个 hint，覆盖 sideEffects 推导出的默认值
   * @param {number=}  cap.timeoutMs     执行超时（默认 15000）
   * @param {boolean=} cap.namespace     true 时强制加 <appId>__ 前缀，默认自动
   * @param {AbortSignal=} cap.signal    注册生命周期信号；abort 时该能力自动注销
   * @param {Function} cap.handler       (args, ctx) => value | Promise<value>  ★ 本地执行
   */
  function defineCapability(cap) {
    if (!cap || typeof cap !== 'object') throw new Error('[HarnessBridge] capability 必须是对象')
    var rawName = String(cap.name || '').trim()
    if (!NAME_RE.test(rawName)) {
      throw new Error(
        '[HarnessBridge] capability.name 非法（首字符须为字母/下划线，其余可含字母数字下划线点横线，最长 128）：' + cap.name,
      )
    }
    var name = normalizeName(rawName)
    if (!cap.description || String(cap.description).trim().length < 4) {
      throw new Error('[HarnessBridge] capability "' + name + '" 缺少 description —— 这是模型判断该不该用的唯一依据，必填')
    }
    if (typeof cap.handler !== 'function') {
      throw new Error('[HarnessBridge] capability "' + name + '" 缺少 handler 函数')
    }
    var sideEffects = cap.sideEffects || 'read'
    if (SIDE_EFFECTS.indexOf(sideEffects) < 0) {
      throw new Error('[HarnessBridge] capability "' + name + '" 的 sideEffects 非法：' + sideEffects)
    }
    var approval = cap.approval || 'auto'
    if (APPROVAL_MODES.indexOf(approval) < 0) {
      throw new Error('[HarnessBridge] capability "' + name + '" 的 approval 非法：' + approval)
    }
    var format = cap.output && cap.output.format
    if (format !== 'text' && format !== 'blocks') format = 'json'
    var maxChars = Number(cap.output && cap.output.maxChars) > 0 ? Number(cap.output.maxChars) : null
    // 别名：同一份 manifest 直投 MCP 时读 inputSchema / outputSchema
    var inputSchema =
      cap.inputSchema || cap.parameters || { type: 'object', additionalProperties: true, properties: {} }
    var outputSchema =
      cap.outputSchema ||
      (cap.output && cap.output.schema) ||
      (format === 'text'
        ? { type: 'string' }
        : format === 'blocks'
          ? { type: 'array', items: { type: 'object', additionalProperties: true } }
          : { type: 'object', additionalProperties: true })
    var defined = {
      name: name,
      title: cap.title ? String(cap.title) : rawName,
      originalName: rawName,
      description: String(cap.description),
      parameters: inputSchema,
      inputSchema: inputSchema,
      output: {
        // 默认宽松 schema：应用不声明也能跑，但强烈建议声明（内核会校验返回值）
        schema: outputSchema,
        format: format,
        maxChars: maxChars,
        // format 已是 json 时渲染结果本身就是 JSON，再附一遍是噪音
        structured: cap.output && cap.output.structured === true && format !== 'json',
        render: (cap.output && cap.output.render) || null,
      },
      outputSchema: outputSchema,
      sideEffects: sideEffects,
      approval: approval,
      annotations: deriveAnnotations(cap, sideEffects, approval),
      timeoutMs: Number(cap.timeoutMs) > 0 ? Number(cap.timeoutMs) : 15000,
      namespace: cap.namespace === true,
      handler: cap.handler,
    }
    // ★ signal 是**本地生命周期**，不属于协议的一部分。
    //   做成不可枚举：adopt() 照样能读到，但它不会出现在 JSON.stringify、
    //   console.log 或任何序列化路径里——「不上行」这件事由结构保证，不靠自觉。
    Object.defineProperty(defined, 'signal', {
      value: cap.signal || null,
      enumerable: false,
      writable: true,
      configurable: true,
    })
    return defined
  }

  /** 去掉 handler，得到可以上行的 manifest 描述。 */
  function toDescriptor(cap) {
    return {
      name: cap.name,
      title: cap.title,
      description: cap.description,
      // 两套字段名指向同一份 schema：老宿主读 parameters，MCP 工具读 inputSchema
      parameters: cap.parameters,
      inputSchema: cap.inputSchema,
      output: {
        schema: cap.output.schema,
        format: cap.output.format,
        maxChars: cap.output.maxChars || undefined,
        structured: cap.output.structured || undefined,
      },
      outputSchema: cap.outputSchema,
      sideEffects: cap.sideEffects,
      approval: cap.approval,
      annotations: cap.annotations,
      timeoutMs: cap.timeoutMs,
      namespace: cap.namespace,
    }
  }

  /* ======================================================================
   * 三·二、声明式能力 —— 从 <form> 派生 schema（P2-6）
   * ----------------------------------------------------------------------
   * 对齐 WebMCP 的 declarative API：老站点不改 JS，只给表单加几个 data-* 属性，
   * 就能被 agent 操作。默认 handler 会「填表 + 触发 input/change + 提交」，
   * 于是**任何**既有表单都立刻变成一个可被 agent 调用的工具。
   *
   * 这对 React / Vue 受控组件也有效：直接改 `el.value` 不会触发框架的 onChange，
   * 必须走原生 setter + 手动派发事件（下面 setNativeValue 就是干这个的）。
   * ==================================================================== */

  /** 绕过框架的 value 拦截，写进原生 setter 再派发事件（React 受控组件必需）。 */
  function setNativeValue(el, value) {
    var proto = null
    try {
      if (typeof HTMLTextAreaElement !== 'undefined' && el instanceof HTMLTextAreaElement)
        proto = HTMLTextAreaElement.prototype
      else if (typeof HTMLSelectElement !== 'undefined' && el instanceof HTMLSelectElement)
        proto = HTMLSelectElement.prototype
      else if (typeof HTMLInputElement !== 'undefined' && el instanceof HTMLInputElement)
        proto = HTMLInputElement.prototype
    } catch (e) {
      proto = null
    }
    var desc = proto && Object.getOwnPropertyDescriptor(proto, 'value')
    if (desc && desc.set) desc.set.call(el, value)
    else el.value = value
  }

  function fireEvents(el, doc) {
    var view = (doc && doc.defaultView) || (typeof window !== 'undefined' ? window : null)
    var mk = function (type) {
      try {
        return new Event(type, { bubbles: true })
      } catch (e) {
        if (view && view.Event) return new view.Event(type, { bubbles: true })
        return null
      }
    }
    var types = ['input', 'change']
    for (var i = 0; i < types.length; i++) {
      var ev = mk(types[i])
      if (ev) el.dispatchEvent(ev)
    }
  }

  var SKIP_INPUT_TYPES = { submit: 1, button: 1, reset: 1, image: 1 }

  /** 从一个表单控件推导 JSON Schema 片段。 */
  function schemaOfControl(el) {
    var tag = String(el.tagName || '').toUpperCase()
    var type = String(el.type || '').toLowerCase()
    var label =
      el.getAttribute('data-harness-description') ||
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.name

    if (tag === 'SELECT') {
      var values = []
      var options = el.options || []
      for (var i = 0; i < options.length; i++) {
        var ov = options[i].value !== undefined && options[i].value !== '' ? options[i].value : options[i].text
        if (values.indexOf(ov) < 0) values.push(ov)
      }
      var sp = { description: label }
      if (el.multiple) {
        sp.type = 'array'
        sp.items = { type: 'string' }
        if (values.length) sp.items.enum = values
      } else {
        sp.type = 'string'
        if (values.length) sp.enum = values
      }
      return sp
    }

    var p = { description: label }
    if (type === 'checkbox') {
      p.type = 'boolean'
      return p
    }
    if (type === 'number' || type === 'range') {
      p.type = 'number'
      if (el.min !== '') p.minimum = Number(el.min)
      if (el.max !== '') p.maximum = Number(el.max)
      if (type === 'range' && el.step !== '') p.multipleOf = Number(el.step)
      return p
    }
    if (type === 'date' || type === 'datetime-local' || type === 'time' || type === 'month' || type === 'week') {
      p.type = 'string'
      p.format = type
      return p
    }
    if (type === 'email') {
      p.type = 'string'
      p.format = 'email'
      return p
    }
    if (type === 'url') {
      p.type = 'string'
      p.format = 'uri'
      return p
    }
    if (type === 'radio') {
      p.type = 'string'
      p.enum = [el.value]
      return p
    }
    if (type === 'file') {
      p.type = 'string'
      p.description = (label || el.name) + '（文件名或 dataURL；文件上传需自定义 handler）'
      return p
    }
    p.type = 'string'
    return p
  }

  /**
   * 把一张 `<form>` 变成一个能力定义（可直接喂给 `defineCapability` / `register`）。
   *
   * 表单上可写的 `data-*`：
   *   data-harness-name          能力名（默认取 form.name || form.id）
   *   data-harness-description   ★ 给模型看的说明（默认取 form 的 aria-label / 标题）
   *   data-harness-title         展示名
   *   data-harness-side-effects  read | write | destructive（默认 write —— 提交表单是写操作）
   *
   * @param {HTMLFormElement} form
   * @param {object=} options
   * @param {string=}   options.name / description / title / sideEffects / approval
   * @param {boolean=}  options.submit   默认 true：填完自动 requestSubmit()
   * @param {AbortSignal=} options.signal 生命周期信号（组件卸载自动注销）
   * @param {Function=} options.handler  自定义 handler；不传则用「填表 + 提交」
   * @returns {object} 能力定义对象
   */
  function fromForm(form, options) {
    if (!form || !form.elements) throw new Error('[HarnessBridge] fromForm 需要一个 <form> 元素')
    var opts2 = options || {}
    var get = function (key) {
      return form.getAttribute ? form.getAttribute('data-harness-' + key) : null
    }
    var name = String(opts2.name || get('name') || form.name || form.id || '').trim()
    if (!name) {
      throw new Error('[HarnessBridge] fromForm 无法确定能力名：请给表单加 name/id，或用 options.name 指定')
    }
    var description = String(
      opts2.description ||
        get('description') ||
        (form.getAttribute && (form.getAttribute('aria-label') || form.getAttribute('title'))) ||
        '',
    ).trim()
    if (description.length < 4) {
      throw new Error(
        '[HarnessBridge] fromForm 缺少 description：请在表单上加 data-harness-description，或用 options.description 指定',
      )
    }

    var properties = {}
    var required = []
    var elements = form.elements
    var radios = {}
    for (var i = 0; i < elements.length; i++) {
      var el = elements[i]
      if (!el || !el.name) continue
      if (el.disabled) continue
      var tag = String(el.tagName || '').toUpperCase()
      if (tag !== 'INPUT' && tag !== 'SELECT' && tag !== 'TEXTAREA') continue
      var type = String(el.type || '').toLowerCase()
      if (SKIP_INPUT_TYPES[type]) continue

      if (type === 'radio') {
        if (radios[el.name]) {
          var vals = radios[el.name].enum
          if (vals.indexOf(el.value) < 0) vals.push(el.value)
          continue
        }
        radios[el.name] = schemaOfControl(el)
        properties[el.name] = radios[el.name]
      } else {
        properties[el.name] = schemaOfControl(el)
      }
      if (el.required) required.push(el.name)
    }

    var inputSchema = { type: 'object', properties: properties }
    if (required.length) inputSchema.required = required

    var defaultHandler = function (args) {
      var doc = form.ownerDocument || (typeof document !== 'undefined' ? document : null)
      var applied = []
      var keys = Object.keys(args || {})
      for (var k = 0; k < keys.length; k++) {
        var fieldName = keys[k]
        var val = args[fieldName]
        var nodes = form.elements[fieldName]
        if (!nodes) continue
        var list = nodes.length !== undefined && nodes.tagName === undefined ? nodes : [nodes]
        for (var j = 0; j < list.length; j++) {
          var node = list[j]
          var t = String(node.type || '').toLowerCase()
          if (t === 'checkbox') {
            var want = val === true || val === 'true' || val === 'on' || val === 1
            if (node.checked !== want) {
              node.checked = want
              fireEvents(node, doc)
            }
          } else if (t === 'radio') {
            if (String(node.value) === String(val) && !node.checked) {
              node.checked = true
              fireEvents(node, doc)
            }
          } else if (node.tagName === 'SELECT' && node.multiple && val && val.length !== undefined) {
            for (var o = 0; o < node.options.length; o++) {
              node.options[o].selected = val.indexOf(node.options[o].value) >= 0
            }
            fireEvents(node, doc)
          } else {
            setNativeValue(node, val === undefined || val === null ? '' : String(val))
            fireEvents(node, doc)
          }
        }
        applied.push(fieldName)
      }

      var willSubmit = opts2.submit !== false
      if (willSubmit && typeof form.requestSubmit === 'function') {
        form.requestSubmit()
      } else if (willSubmit && typeof form.submit === 'function') {
        form.submit()
      }
      return { applied: applied, submitted: willSubmit }
    }

    return defineCapability({
      name: name,
      title: opts2.title || get('title') || name,
      description: description,
      parameters: opts2.parameters || inputSchema,
      output: opts2.output || { format: 'json' },
      sideEffects: opts2.sideEffects || get('side-effects') || 'write',
      approval: opts2.approval || get('approval') || 'auto',
      timeoutMs: opts2.timeoutMs,
      namespace: opts2.namespace,
      annotations: opts2.annotations,
      signal: opts2.signal || null,
      handler: opts2.handler || defaultHandler,
    })
  }

  /* ======================================================================
   * 三·三、错误分类与内容块
   * ----------------------------------------------------------------------
   * 错误分两类（对齐 MCP 的协议错误 / 工具执行错误）：
   *   execution —— 业务逻辑说不行（库存不足、订单不存在）。模型必须看到，才能改方案。
   *   protocol  —— 调用根本没跑成（参数畸形、依赖缺失）。模型不该据此改业务判断。
   * handler 里 `throw HarnessBridge.protocolError('...')` 可显式指定；默认按 execution。
   * ==================================================================== */

  /** 构造一个「业务反馈」错误——模型会看到它并自我纠正。 */
  function executionError(message) {
    var e = new Error(String(message))
    e.errorKind = 'execution'
    return e
  }

  /** 构造一个「调用没跑成」错误——属于系统层面，模型不该据此改业务判断。 */
  function protocolError(message) {
    var e = new Error(String(message))
    e.errorKind = 'protocol'
    return e
  }

  /**
   * 内容块构造助手（配合 output.format: 'blocks' 使用）。
   * 内核会把它们渲染成模型能读的文本；图片/音频只给摘要，不塞 base64 进上下文。
   */
  function textBlock(text) {
    return { type: 'text', text: String(text == null ? '' : text) }
  }
  function imageBlock(data, mimeType) {
    return { type: 'image', data: String(data || ''), mimeType: mimeType || 'image/png' }
  }
  function audioBlock(data, mimeType) {
    return { type: 'audio', data: String(data || ''), mimeType: mimeType || 'audio/mpeg' }
  }
  function linkBlock(uri, name, mimeType) {
    return { type: 'resource_link', uri: String(uri || ''), name: name ? String(name) : undefined, mimeType: mimeType }
  }

  /* ======================================================================
   * 四、迷你事件总线
   * ==================================================================== */

  function createEmitter() {
    var map = {}
    function off(type, fn) {
      var arr = map[type]
      if (!arr) return
      var i = arr.indexOf(fn)
      if (i >= 0) arr.splice(i, 1)
    }
    return {
      /** 订阅；返回一个取消订阅的函数（也可用 off(type, fn)）。 */
      on: function (type, fn) {
        ;(map[type] || (map[type] = [])).push(fn)
        return function () {
          off(type, fn)
        }
      },
      off: off,
      emit: function (type, payload) {
        var arr = map[type]
        if (!arr) return
        // 复制一份再遍历：监听器里调用 off 不会打乱本次派发
        var snapshot = arr.slice()
        for (var i = 0; i < snapshot.length; i++) {
          try {
            snapshot[i](payload)
          } catch (e) {
            /* 单个监听器出错不影响其它监听器 */
          }
        }
      },
      clear: function () {
        map = {}
      },
    }
  }

  /* ======================================================================
   * 五、Bridge —— 核心
   * ==================================================================== */

  /**
   * 连接到内核并把自己「注册成一个可被 agent 操作的应用」。
   *
   * @param {object} options
   * @param {string}  options.baseUrl        内核地址，或你自己的后端转发路径如 '/agent'
   * @param {string}  options.appId          ★ 应用标识，全局唯一（如 'shop-admin'）
   * @param {string=} options.appName        应用展示名
   * @param {string=} options.appVersion     应用版本
   * @param {Array=}  options.capabilities   能力清单（defineCapability 的产物）
   * @param {string=} options.token          鉴权 token（放 Authorization 头）
   * @param {Function=} options.tokenProvider ★ 短时票据：() => token | { token, expiresAt }
   *        传了它就以此为准；每次请求前按需解析，401 时自动刷新重试一次。
   *        推荐浏览器端只用它——长时 token 不进 localStorage，泄露的票据会自己过期。
   * @param {string=} options.clientId       客户端实例 id；同 appId 多标签页靠它区分
   * @param {string=} options.sessionId      指定会话；不传则新建并记忆
   * @param {string=} options.storageKey     localStorage 键名；null 禁用记忆（多用户必须）
   * @param {object=} options.context        初始上下文（当前页面 / 选中项 / 用户）
   * @param {object=} options.headers        附加请求头
   * @param {Function=} options.fetch        自定义 fetch
   * @param {boolean=} options.chat          是否允许对话，默认 true
   * @param {Function=} options.onEvent      (evt) => void，所有事件的统一出口
   */
  async function connect(options) {
    var opts = options || {}
    var baseUrl = trimSlash(opts.baseUrl || '')
    if (!baseUrl) throw new Error('[HarnessBridge] baseUrl 必填')
    var appId = String(opts.appId || '').trim()
    if (!appId) throw new Error('[HarnessBridge] appId 必填 —— 它是应用在内核里的唯一身份')

    var doFetch = opts.fetch || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null)
    if (!doFetch) throw new Error('[HarnessBridge] 当前环境没有 fetch，请通过 options.fetch 注入')

    var storageKey = opts.storageKey === undefined ? 'harness-bridge-session' : opts.storageKey
    var clientIdKey = storageKey ? storageKey + '-client' : null
    var eventIdKey = storageKey ? storageKey + '-eventid' : null
    var extraHeaders = opts.headers || {}
    var emitter = createEmitter()
    var handlers = {} // name -> handler
    var descriptors = [] // 上行用
    var seenCalls = {} // callId 去重
    var seenOrder = [] // 去重表的插入顺序，用于有界淘汰（避免长会话内存无界增长）
    var SEEN_MAX = 500
    var inflight = {} // callId -> AbortController（本地在途的能力执行）
    var controller = null // 当前 SSE 请求的 AbortController
    var context = opts.context ? JSON.parse(JSON.stringify(opts.context)) : {}
    var connected = false
    var hostInfo = null // 宿主能力协商结果（GET /v1/capabilities）
    var lastEventId = 0 // 已收到的最大 SSE 帧 id（断线续传用）

    /** 记录一个已处理的 callId，超出上限时淘汰最早的。 */
    function rememberCall(callId) {
      if (seenCalls[callId]) return
      seenCalls[callId] = true
      seenOrder.push(callId)
      if (seenOrder.length > SEEN_MAX) delete seenCalls[seenOrder.shift()]
    }

    var clientId = opts.clientId || readStore(clientIdKey) || uid('client')
    writeStore(clientIdKey, clientId)

    var sessionId = opts.sessionId || readStore(storageKey)
    var sessionAppId = null
    lastEventId = Number(readStore(eventIdKey)) || 0

    /* ---- 存储 ---- */
    function readStore(key) {
      if (!key || typeof localStorage === 'undefined') return null
      try {
        return localStorage.getItem(key)
      } catch (e) {
        return null
      }
    }
    function writeStore(key, value) {
      if (!key || typeof localStorage === 'undefined') return
      try {
        if (value) localStorage.setItem(key, value)
        else localStorage.removeItem(key)
      } catch (e) {
        /* 隐私模式静默失败 */
      }
    }

    /**
     * 记录一帧的 id，供断线续传用。
     * 内存里每帧都更新（网络抖动时能精确补发）；落盘只在「非 delta 帧」和
     * 一轮结束时做——delta 太频繁，落盘会拖慢渲染。
     * 落盘的语义是「上一个已完成/已确定的事件边界」，页面刷新后从边界重放，
     * 此时前端是全新 DOM，重放整轮不会产生重复内容。
     */
    function noteEventId(frame) {
      var id = Number(frame && frame.id)
      if (!(id > 0)) return
      if (id > lastEventId) lastEventId = id
      if (frame.type !== 'text-delta' && frame.type !== 'reasoning-delta') {
        writeStore(eventIdKey, String(lastEventId))
      }
    }
    function persistEventId() {
      writeStore(eventIdKey, String(lastEventId))
    }

    /**
     * 切换当前会话。
     * ★ 换会话必须**作废续传游标**：帧 id 是「会话内单调递增」的，
     *   拿 A 会话的 id 去 resume B 会话，要么跳过一堆帧、要么补一堆重复帧。
     */
    function setSession(id) {
      var next = id ? String(id) : null
      if (next !== sessionId) {
        sessionId = next
        lastEventId = 0
        writeStore(eventIdKey, null)
      }
      writeStore(storageKey, sessionId)
    }

    /* ---- 请求头与统一请求层 ---- */

    var cachedToken = opts.token || null
    var tokenExpiresAt = 0
    var tokenPromise = null

    /**
     * 解析鉴权 token。
     * - 传了 `tokenProvider`：以它为准（可返回字符串，或 `{ token, expiresAt }`）
     * - 只传了 `token`：用静态 token（v2 的老行为）
     * 结果带 TTL 缓存；`force=true` 时忽略缓存强制刷新（401 重试用）。
     */
    function ensureToken(force) {
      if (!opts.tokenProvider) {
        cachedToken = opts.token || null
        return Promise.resolve(cachedToken)
      }
      var fresh = !force && cachedToken && (!tokenExpiresAt || Date.now() < tokenExpiresAt - 5000)
      if (fresh) return Promise.resolve(cachedToken)
      if (tokenPromise) return tokenPromise
      tokenPromise = Promise.resolve()
        .then(function () {
          return opts.tokenProvider()
        })
        .then(function (out) {
          if (out && typeof out === 'object') {
            cachedToken = out.token ? String(out.token) : null
            var exp = Number(out.expiresAt)
            // 允许秒级时间戳（OAuth 常见）
            if (exp > 0 && exp < 1e12) exp *= 1000
            tokenExpiresAt = exp > 0 ? exp : 0
          } else {
            cachedToken = out ? String(out) : null
            tokenExpiresAt = 0
          }
          tokenPromise = null
          return cachedToken
        })
        .catch(function (e) {
          tokenPromise = null
          throw e
        })
      return tokenPromise
    }

    function buildHeaders(extra) {
      var head = { 'content-type': 'application/json' }
      head['x-harness-protocol'] = PROTOCOL
      head['x-harness-app'] = appId
      head['x-harness-client'] = clientId
      if (cachedToken) head['authorization'] = 'Bearer ' + cachedToken
      // 用 Object.entries 而不是 for...in：for...in 会把原型链上的可枚举属性
      // 也一起复制进请求头（`Object.prototype` 被污染时会静默带上垃圾头）。
      var headerSources = [extraHeaders, extra]
      for (var hi = 0; hi < headerSources.length; hi++) {
        var src = headerSources[hi]
        if (!src || typeof src !== 'object') continue
        var hs = Object.entries(src)
        for (var hj = 0; hj < hs.length; hj++) {
          if (hs[hj][1] === undefined || hs[hj][1] === null) continue
          head[hs[hj][0]] = hs[hj][1]
        }
      }
      return head
    }

    function api(path) {
      return baseUrl + path
    }

    /**
     * 统一请求入口：解析 token → 发请求 → 401 且配了 tokenProvider 时刷新票据重试一次。
     * 所有 /v1 调用都走这里，保证「短时票据过期」对业务代码完全透明。
     */
    async function request(path, init) {
      var opts2 = init || {}
      await ensureToken(false)
      var res = await doFetch(api(path), {
        method: opts2.method,
        body: opts2.body,
        signal: opts2.signal,
        headers: buildHeaders(opts2.headers),
      })
      if (res.status === 401 && opts.tokenProvider) {
        try {
          await ensureToken(true)
        } catch (e) {
          return res
        }
        res = await doFetch(api(path), {
          method: opts2.method,
          body: opts2.body,
          signal: opts2.signal,
          headers: buildHeaders(opts2.headers),
        })
      }
      return res
    }

    function fail(message) {
      var err = { message: message }
      emitter.emit('error', err)
      if (opts.onEvent) opts.onEvent({ type: 'error', data: err })
      return err
    }

    /* ==================================================================
     * 5.1 能力注册
     * ================================================================ */

    var signalWatchers = [] // [{ signal, onAbort }]，disconnect 时统一摘掉

    /** 绑一个 AbortSignal；已 abort 的立刻回调，否则监听（兼容老环境的 onabort 写法）。 */
    function watchSignal(signal, onAbort) {
      if (!signal) return
      if (signal.aborted) {
        try {
          onAbort()
        } catch (e) {
          /* 忽略 */
        }
        return
      }
      if (typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', onAbort)
        signalWatchers.push({ signal: signal, onAbort: onAbort })
        // 竞态兜底：abort 事件可能发生在「读 aborted」与「挂监听」之间，那一下是收不到的
        if (signal.aborted) {
          try {
            onAbort()
          } catch (e) {
            /* 忽略 */
          }
        }
      } else if ('onabort' in signal) {
        var prev = signal.onabort
        signal.onabort = function (ev) {
          if (typeof prev === 'function') prev.call(this, ev)
          onAbort()
        }
        signalWatchers.push({
          signal: signal,
          onAbort: function () {
            signal.onabort = prev || null
          },
        })
      }
    }

    function unwatchAll() {
      for (var i = 0; i < signalWatchers.length; i++) {
        var w = signalWatchers[i]
        try {
          if (typeof w.signal.removeEventListener === 'function') w.signal.removeEventListener('abort', w.onAbort)
          else w.onAbort() // onabort 写法的清理函数
        } catch (e) {
          /* 忽略 */
        }
      }
      signalWatchers = []
    }

    /** 从本地登记表里摘掉若干能力（不动网络）。返回真正被摘掉的名字。 */
    function removeLocal(names) {
      var removed = []
      for (var i = 0; i < names.length; i++) {
        var n = String(names[i])
        var before = descriptors.length
        descriptors = descriptors.filter(function (d) {
          return d.name !== n
        })
        if (descriptors.length !== before) removed.push(n)
        delete handlers[n]
      }
      return removed
    }

    /**
     * 本地登记能力（不发网络请求）。
     * ★ 带 `signal` 的能力会在 signal abort 时**自动下线**并热更新 manifest ——
     *   SPA 路由切换 / 组件卸载时不必再手动调 unregister，这是对齐 WebMCP 的关键一步。
     * @returns {string[]} 被跳过（signal 在登记前就已 abort）的能力名
     */
    function adopt(caps) {
      var skipped = []
      for (var i = 0; i < caps.length; i++) {
        var cap = caps[i]
        if (cap.signal && cap.signal.aborted) {
          skipped.push(cap.name)
          continue
        }
        handlers[cap.name] = cap.handler
        descriptors.push(toDescriptor(cap))
        if (cap.signal) {
          // 闭包捕获：每个能力只摘自己
          ;(function (boundName, boundSignal) {
            watchSignal(boundSignal, function () {
              var gone = removeLocal([boundName])
              if (!gone.length) return
              emitter.emit('capabilities-changed', {
                capabilities: [],
                removed: gone,
                reason: 'signal-abort',
              })
              schedulePush()
            })
          })(cap.name, cap.signal)
        }
      }
      return skipped
    }

    /**
     * 串行化的 manifest 热更新：同一轮里多次 abort / 注册只发一次上行请求。
     * 返回的 promise 可用于测试与「等热更新落地」。
     */
    var pushQueued = false
    var manifestChain = Promise.resolve()
    function schedulePush() {
      if (pushQueued) return manifestChain
      pushQueued = true
      manifestChain = manifestChain
        .then(function () {
          pushQueued = false
          if (!connected) return null
          return pushManifest()
        })
        .catch(function (e) {
          fail('能力热更新失败：' + ((e && e.message) || e))
        })
      return manifestChain
    }

    /** 把当前 descriptors 上报内核，内核据此动态注册工具。 */
    async function pushManifest() {
      var res = await request('/v1/apps/' + encodeURIComponent(appId) + '/manifest', {
        method: 'POST',
        body: JSON.stringify({
          protocol: PROTOCOL,
          appId: appId,
          appName: opts.appName || appId,
          appVersion: opts.appVersion || VERSION,
          clientId: clientId,
          capabilities: descriptors,
        }),
      })
      if (!res.ok) {
        var body = ''
        try {
          body = await res.text()
        } catch (e) {
          /* 忽略 */
        }
        throw new Error('能力上报失败 HTTP ' + res.status + ' ' + body.slice(0, 300))
      }
      return res.json()
    }

    /**
     * 运行时追加能力并热更新（不需要重连、不需要重启内核）。
     *
     * @param {object|object[]} caps
     * @param {{signal?: AbortSignal}=} registerOptions
     *        整批生命周期信号。abort 时**整批**下线——React 里就是
     *        `useEffect(() => { bridge.register(caps, { signal: ctl.signal }) ; return () => ctl.abort() })`
     */
    async function register(caps, registerOptions) {
      var list = Array.isArray(caps) ? caps : [caps]
      var ro = registerOptions || {}
      var skipped = adopt(list)
      var names = []
      for (var i = 0; i < list.length; i++) {
        if (skipped.indexOf(list[i].name) < 0) names.push(list[i].name)
      }

      if (ro.signal) {
        watchSignal(ro.signal, function () {
          var gone = removeLocal(names)
          if (!gone.length) return
          emitter.emit('capabilities-changed', { capabilities: [], removed: gone, reason: 'signal-abort' })
          schedulePush()
        })
      }

      var out = await pushManifest()
      emitter.emit('capabilities-changed', { capabilities: names, skipped: skipped })
      return out
    }

    /** 移除能力。 */
    async function unregister(names) {
      var list = (Array.isArray(names) ? names : [names]).map(String)
      removeLocal(list)
      return pushManifest()
    }

    /* ==================================================================
     * 5.2 上下文 —— 让 agent 知道「用户现在在看什么」
     * ================================================================ */

    async function setContext(next) {
      // 先深拷贝（切断与调用方的引用），再按键白名单过滤掉 __proto__ 等
      var clone = next ? JSON.parse(JSON.stringify(next)) : {}
      context = sanitizeContext(clone)
      await syncContext('replace')
      return context
    }

    /**
     * 增量更新上下文（只上行变更的键，不再整份重传）。
     * `null` 值表示**删除该键**（对齐 JSON Merge Patch RFC 7386）。
     */
    async function patchContext(patch) {
      var delta = {}
      var applied = []
      if (patch && typeof patch === 'object') {
        var entries = Object.entries(patch)
        for (var i = 0; i < entries.length; i++) {
          var k = entries[i][0]
          var v = entries[i][1]
          // 跳过 __proto__ / constructor / prototype，避免本地 context 的原型被换掉
          if (!contextKeyAllowed(k)) continue
          // null 也要带上：服务端靠「值为 null」判断这是删除（JSON Merge Patch）
          delta[k] = v
          if (v === null || v === undefined) delete context[k]
          else context[k] = v
          applied.push(k)
        }
      }
      if (!applied.length) return context
      await syncContext('merge', delta)
      return context
    }

    async function clearContext() {
      context = {}
      await syncContext('replace')
      return context
    }

    /** 把上下文推给内核；没有会话时先攒着，等会话建立随首个 chat 一起带上。 */
    async function syncContext(mode, patch) {
      if (!sessionId || !connected) return
      try {
        if (mode === 'merge') {
          await request('/v1/context', {
            method: 'PATCH',
            body: JSON.stringify({ sessionId: sessionId, appId: appId, clientId: clientId, merge: patch || {} }),
          })
        } else {
          await request('/v1/context', {
            method: 'POST',
            body: JSON.stringify({ sessionId: sessionId, appId: appId, clientId: clientId, context: context }),
          })
        }
      } catch (e) {
        fail('上下文同步失败：' + (e && e.message))
      }
    }

    /* ==================================================================
     * 5.3 客户端工具执行 —— 内核反向调用的落点
     * ================================================================ */

    /** 内核要求执行某个能力：本地跑 handler，把结果回传。 */
    async function serveInvoke(inv) {
      var callId = String(inv.callId || '')
      var name = String(inv.name || '')
      if (!callId) return
      if (seenCalls[callId]) return // 重放保护
      rememberCall(callId)

      var handler = handlers[name]
      if (!handler) {
        return postResult(callId, { ok: false, error: '应用未注册该能力：' + name, errorKind: 'protocol' })
      }

      var timeoutMs = Number(inv.timeoutMs) > 0 ? Number(inv.timeoutMs) : 15000
      var args = inv.arguments
      if (typeof args === 'string') args = safeJson(args) || {}

      // ★ 每次调用一个 AbortController：内核推 tool-cancel、或本地 abort() 时，
      //   handler 通过 ctx.signal 收到中断信号（v2 完全缺失这条通道）。
      var abortCtl = typeof AbortController !== 'undefined' ? new AbortController() : null
      inflight[callId] = abortCtl

      var lastProgressAt = 0
      /**
       * 长任务回传进度（对齐 MCP 的 notifications/progress）。
       * 节流 200ms —— 进度是「给用户看的」，不是审计记录，刷太密只是浪费带宽。
       */
      function reportProgress(progress, info) {
        var now = Date.now()
        if (now - lastProgressAt < 200) return false
        lastProgressAt = now
        var p = Number(progress)
        if (!isFinite(p)) return false
        var extra = info || {}
        var body = {
          callId: callId,
          appId: appId,
          clientId: clientId,
          progress: p,
          total: Number(extra.total) > 0 ? Number(extra.total) : undefined,
          message: extra.message ? String(extra.message) : undefined,
          stage: extra.stage ? String(extra.stage) : undefined,
        }
        request('/v1/tool-progress', { method: 'POST', body: JSON.stringify(body) }).catch(function (e) {
          fail('进度回传失败（callId=' + callId + '）：' + (e && e.message))
        })
        // 本地也 emit 一次：应用不必等内核把帧转回来就能立刻更新进度条。
        // 形状与 SSE 帧保持一致（都带 capability），否则消费方要写两套分支。
        emitter.emit('tool-progress', {
          callId: callId,
          appId: appId,
          clientId: clientId,
          capability: name,
          progress: p,
          total: body.total,
          message: body.message,
          stage: body.stage,
          local: true,
        })
        return true
      }

      var timer = null
      var timedOut = false
      try {
        var value = await Promise.race([
          Promise.resolve(
            handler(args, {
              appId: appId,
              clientId: clientId,
              callId: callId,
              context: context,
              signal: abortCtl ? abortCtl.signal : undefined,
              // 进度回传（内核不支持时静默无效，SDK 不报错）
              progressToken: inv.progressToken,
              reportProgress: reportProgress,
            }),
          ),
          new Promise(function (_, reject) {
            timer = setTimeout(function () {
              timedOut = true
              reject(new Error('能力 ' + name + ' 执行超时（' + timeoutMs + 'ms）'))
            }, timeoutMs)
          }),
        ])
        if (timer) clearTimeout(timer)
        delete inflight[callId]
        emitter.emit('capability-executed', { callId: callId, name: name, ok: true })
        return postResult(callId, { ok: true, value: value === undefined ? null : value })
      } catch (error) {
        if (timer) clearTimeout(timer)
        delete inflight[callId]
        var message = String((error && error.message) || error)
        var aborted = !!(abortCtl && abortCtl.signal.aborted) && !timedOut
        // 超时 / 被中断是系统层面的事；handler 自己抛错默认算业务反馈
        var errorKind = timedOut || aborted ? 'protocol' : error && error.errorKind === 'protocol' ? 'protocol' : 'execution'
        emitter.emit('capability-executed', {
          callId: callId,
          name: name,
          ok: false,
          aborted: aborted,
          errorKind: errorKind,
          error: message,
        })
        // 已取消的调用不再回传：内核那边早判它失败了，回传只会拿到 404
        if (aborted) return
        return postResult(callId, { ok: false, error: message, errorKind: errorKind })
      }
    }

    /** 中断所有本地在途的能力执行。返回中断的数量。 */
    function abortInflight(reason) {
      var ids = Object.keys(inflight)
      for (var i = 0; i < ids.length; i++) {
        var ctl = inflight[ids[i]]
        if (ctl && typeof ctl.abort === 'function') {
          try {
            ctl.abort(reason)
          } catch (e) {
            /* 忽略 */
          }
        }
        delete inflight[ids[i]]
      }
      return ids.length
    }

    function postResult(callId, payload) {
      return request('/v1/tool-result', {
        method: 'POST',
        body: JSON.stringify({
          callId: callId,
          appId: appId,
          clientId: clientId,
          ok: !!payload.ok,
          value: payload.value,
          error: payload.error,
          errorKind: payload.errorKind,
        }),
      }).catch(function (e) {
        fail('工具结果回传失败（callId=' + callId + '）：' + (e && e.message))
      })
    }

    /* ==================================================================
     * 5.4 对话（流式）
     * ================================================================ */

    /**
     * 共享的流式回合：发一次 /v1/chat，把 SSE 帧归一化后 yield 出去。
     * chat() 与 resume() 都走这里，保证两条路径行为完全一致。
     */
    async function* streamTurn(payload, externalSignal) {
      controller = new AbortController()
      var signal = externalSignal || controller.signal

      var res
      try {
        res = await request('/v1/chat', {
          method: 'POST',
          body: JSON.stringify(payload),
          signal: signal,
        })
      } catch (error) {
        controller = null
        var m = { message: '无法连接内核：' + (error && error.message) }
        emitter.emit('error', m)
        yield { type: 'error', data: m }
        return
      }

      if (!res.ok) {
        var body = ''
        try {
          body = await res.text()
        } catch (e) {
          /* 忽略 */
        }
        controller = null
        var em = { message: 'HTTP ' + res.status + ' ' + body.slice(0, 300) }
        emitter.emit('error', em)
        yield { type: 'error', data: em }
        return
      }

      var returned = res.headers && res.headers.get ? res.headers.get('x-session-id') : null
      if (returned) {
        setSession(returned)
        sessionAppId = payload.appId || appId
      }

      var reader = res.body.getReader()
      var decoder = new TextDecoder()
      var buffer = ''

      try {
        for (;;) {
          var chunk = await reader.read()
          if (chunk.done) break
          buffer += decoder.decode(chunk.value, { stream: true })
          var frames = buffer.split('\n\n')
          buffer = frames.pop()
          for (var i = 0; i < frames.length; i++) {
            var frame = parseFrame(frames[i])
            if (!frame) continue
            noteEventId(frame)

            // ★ 内核取消某次能力调用：中断本地在途的 handler（handler 收到 signal.aborted）
            if (frame.type === 'tool-cancel') {
              var cancelId = String((frame.data && frame.data.callId) || '')
              var ctl = inflight[cancelId]
              if (ctl && typeof ctl.abort === 'function') {
                try {
                  ctl.abort((frame.data && frame.data.reason) || 'kernel-cancel')
                } catch (e) {
                  /* 忽略 */
                }
              }
              delete inflight[cancelId]
              emitter.emit('tool-cancel', frame.data)
              if (opts.onEvent) opts.onEvent({ type: 'tool-cancel', data: frame.data })
              continue
            }

            // ★ 关键分支：内核要求本地执行能力。Bridge 自己接住，不打扰业务代码。
            if (frame.type === 'tool-invoke') {
              // 续传重放的调用：同一页面会话内由 seenCalls 去重（不会重复执行）；
              // 整页刷新后无法判断是否已执行过，按「至少一次」语义再执行一遍，
              // 并通过 tool-replayed 事件告知应用，让它自行决定是否幂等处理。
              if (frame.data && frame.data.replayed) emitter.emit('tool-replayed', frame.data)
              emitter.emit('tool-invoke', frame.data)
              if (opts.onEvent) opts.onEvent({ type: 'tool-invoke', data: frame.data })
              void serveInvoke(frame.data)
              continue
            }

            // ★ 进度（P2-4）：长任务回传的进度帧，转给应用渲染进度条
            if (frame.type === 'tool-progress') {
              emitter.emit('tool-progress', frame.data)
              if (opts.onEvent) opts.onEvent({ type: 'tool-progress', data: frame.data })
              continue
            }

            // ★ 状态同步（P2-3）：agent 写回界面状态。
            //   这里把 delta 合并进本地 context（让后续 chat 带上最新状态），
            //   再交给应用去真正改 UI —— SDK 不碰 DOM，这是定位边界。
            if (frame.type === 'state-delta') {
              var delta = frame.data || {}
              if (delta.replace === true) {
                context = {}
                if (delta.patch && typeof delta.patch === 'object') mergeOwn(context, delta.patch)
              } else if (delta.patch && typeof delta.patch === 'object') {
                // 走统一入口：内核侧的 patch 已经过滤过一遍，客户端再过滤一次
                // （纵深防御——patch 也可能是别的实现推过来的）
                mergeOwn(context, delta.patch)
              }
              emitter.emit('state-delta', {
                patch: delta.patch || {},
                replace: delta.replace === true,
                context: JSON.parse(JSON.stringify(context)),
                by: delta.by || 'agent',
                at: delta.at,
              })
              if (opts.onEvent) opts.onEvent({ type: 'state-delta', data: delta })
              continue
            }

            var evt = normalizeEvent(frame)
            emitter.emit(evt.type, evt.data)
            if (opts.onEvent) opts.onEvent(evt)
            yield evt
          }
        }
      } catch (error) {
        if (!(error && error.name === 'AbortError')) {
          var ce = { message: String((error && error.message) || error) }
          emitter.emit('error', ce)
          yield { type: 'error', data: ce }
        }
      } finally {
        // ★ 提前 break / return 时，生成器的 finally 会跑，但 fetch 连接不会自己关。
        //   不主动 abort 的话，内核会一直以为「还有人在听」，白跑到底。
        //   （正常跑完时 abort 是无害的空操作。）
        try {
          if (controller) controller.abort()
        } catch (e) {
          /* 忽略 */
        }
        controller = null
        persistEventId()
        // 流都结束了还有在途调用 → 内核显然已经不等了，别再让 handler 空跑。
        // 正常路径下内核会先等到 tool-result 才 done，所以这里通常是空操作；
        // 它兜的是「网络抖动导致流中断」这类拿不到 tool-cancel 的场景。
        abortInflight('stream-closed')
      }
    }

    async function* chat(text, chatOptions) {
      if (opts.chat === false) {
        yield { type: 'error', data: { message: '本 bridge 以 chat:false 创建，不允许对话' } }
        return
      }
      var co = chatOptions || {}
      if (co.sessionId) setSession(co.sessionId)
      if (co.context) {
        // 走统一入口：跳过 __proto__ 等键，且不复制继承属性
        mergeOwn(context, co.context)
      }

      yield* streamTurn(
        {
          text: text,
          sessionId: sessionId || undefined,
          appId: appId,
          clientId: clientId,
          context: context,
        },
        co.signal,
      )
    }

    /**
     * 断线续传：重连同一会话，把 `lastEventId` 之后的帧补回来。
     *
     * 两种场景：
     *   - 网络抖动（页面没刷新）：内存里的 lastEventId 是最新的 → 精确补发，不重复。
     *   - 页面刷新：从落盘的事件边界重放；此时前端 DOM 是全新的，
     *     所以重放整轮是**正确**的，不会出现重复内容。
     *
     * 宿主不支持（`features.resumable === false`）时直接报错，不静默假装成功。
     *
     * @param {{ sessionId?: string, lastEventId?: number, signal?: AbortSignal }=} resumeOptions
     */
    async function* resume(resumeOptions) {
      if (opts.chat === false) {
        yield { type: 'error', data: { message: '本 bridge 以 chat:false 创建，不允许对话' } }
        return
      }
      var ro = resumeOptions || {}
      if (ro.sessionId) setSession(ro.sessionId)
      if (!sessionId) {
        yield { type: 'error', data: { message: '没有可续传的会话：sessionId 为空' } }
        return
      }
      if (hostInfo && hostInfo.features && hostInfo.features.resumable === false) {
        yield {
          type: 'error',
          data: { message: '宿主不支持断线续传（features.resumable = false）' },
        }
        return
      }
      var after = ro.lastEventId != null ? Number(ro.lastEventId) || 0 : lastEventId
      yield* streamTurn(
        {
          resume: true,
          lastEventId: after,
          sessionId: sessionId,
          appId: appId,
          clientId: clientId,
          context: context,
        },
        ro.signal,
      )
    }

    /** 一次性拿完整回答（内部把流收完）。 */
    async function send(text, sendOptions) {
      var collected = ''
      var events = []
      for await (var evt of chat(text, sendOptions)) {
        events.push(evt)
        if (evt.type === 'text-delta' && evt.data && evt.data.text) collected += evt.data.text
        if (evt.type === 'assistant-message') {
          var full = textOfMessage(evt.data && evt.data.message)
          if (full) collected = full
        }
      }
      return { text: collected, events: events, sessionId: sessionId }
    }

    /**
     * 停止本轮生成。
     * ★ 同时中断所有本地在途的能力执行——否则用户点了「停止」，
     *   浏览器里的写操作还在继续跑（v2 的行为）。
     */
    function abort() {
      if (controller) controller.abort()
      return abortInflight('local-abort')
    }

    /* ==================================================================
     * 5.5 审批与观测
     * ================================================================ */

    /**
     * 审批决策。
     * @param {string} id  审批请求 id
     * @param {'allow-once'|'allow-always'|'deny'|'allow'} decision
     *        'allow' 等价于 'allow-once'（v2 的旧写法，继续兼容）
     */
    async function approve(id, decision) {
      var d = decision === 'allow' ? 'allow-once' : decision
      if (d !== 'allow-once' && d !== 'allow-always' && d !== 'deny') d = 'deny'
      var res = await request('/v1/approve', {
        method: 'POST',
        body: JSON.stringify({ id: id, decision: d, appId: appId, clientId: clientId }),
      })
      return res.json()
    }

    /**
     * 能力协商：问宿主支持哪些可选特性。
     * 老宿主没有这个端点，失败不影响主流程。
     */
    async function hostCapabilities() {
      var res = await request('/v1/capabilities', {})
      if (!res.ok) throw new Error('HTTP ' + res.status)
      return res.json()
    }

    async function health() {
      var res = await request('/healthz', {})
      return res.json()
    }

    async function tools() {
      var res = await request('/v1/tools', {})
      return res.json()
    }

    async function apps() {
      var res = await request('/v1/apps', {})
      return res.json()
    }

    async function disconnect() {
      abort()
      unwatchAll()
      connected = false
      try {
        await request('/v1/apps/' + encodeURIComponent(appId) + '?clientId=' + encodeURIComponent(clientId), {
          method: 'DELETE',
        })
      } catch (e) {
        /* 断线注销失败无所谓 */
      }
      emitter.emit('disconnected', { appId: appId, clientId: clientId })
    }

    /* ==================================================================
     * 5.6 初始化
     * ================================================================ */

    if (Array.isArray(opts.capabilities) && opts.capabilities.length) adopt(opts.capabilities)

    // 能力协商：先问宿主支持什么，再上报能力。老宿主没有 /v1/capabilities，忽略即可。
    try {
      hostInfo = await hostCapabilities()
    } catch (error) {
      hostInfo = null
    }

    var manifestResult = { registered: [] }
    if (descriptors.length) {
      try {
        manifestResult = await pushManifest()
      } catch (error) {
        fail(String((error && error.message) || error))
      }
    }
    connected = true
    emitter.emit('connected', {
      appId: appId,
      clientId: clientId,
      capabilities: descriptors.map(function (d) { return d.name }),
      host: hostInfo,
    })

    var bridge = {
      version: VERSION,
      protocol: PROTOCOL,

      /* 身份 */
      get appId() {
        return appId
      },
      get clientId() {
        return clientId
      },
      get baseUrl() {
        return baseUrl
      },
      set baseUrl(value) {
        baseUrl = trimSlash(value || '')
      },
      get sessionId() {
        return sessionId
      },
      set sessionId(value) {
        setSession(value)
      },
      get connected() {
        return connected
      },
      /** 宿主能力协商结果（null = 老宿主，或拉取失败）。 */
      get host() {
        return hostInfo
      },
      get protocolVersion() {
        return PROTOCOL
      },
      get compatibleProtocols() {
        return COMPATIBLE_PROTOCOLS.slice()
      },
      /** 当前在本地执行中、尚未回传结果的客户端能力数量。 */
      get inflightCount() {
        return Object.keys(inflight).length
      },
      /** 已收到的最大 SSE 帧 id（断线续传的游标）。 */
      get lastEventId() {
        return lastEventId
      },

      /* 能力 */
      capabilities: function () {
        return descriptors.slice()
      },
      register: register,
      unregister: unregister,
      /** 声明式能力：把一张 <form> 变成能力定义（P2-6）。 */
      fromForm: fromForm,
      /** 等所有挂起的 manifest 热更新落地（测试 / 优雅退出用）。 */
      whenSynced: function () {
        return manifestChain
      },

      /* 上下文 */
      getContext: function () {
        return JSON.parse(JSON.stringify(context))
      },
      setContext: setContext,
      patchContext: patchContext,
      clearContext: clearContext,

      /* 对话 */
      chat: chat,
      send: send,
      resume: resume,
      abort: abort,
      reset: function () {
        setSession(null)
      },

      /* 审批 / 观测 */
      approve: approve,
      health: health,
      tools: tools,
      apps: apps,
      hostCapabilities: hostCapabilities,
      /** 中断所有本地在途的能力执行；返回中断数量。 */
      cancelInflight: function (reason) {
        return abortInflight(reason || 'local-abort')
      },

      /* 事件 */
      on: emitter.on,
      off: emitter.off,
      disconnect: disconnect,
      manifest: manifestResult,
    }

    return bridge
  }

  /* ======================================================================
   * 六、兼容层：v1 的 createClient（不带能力，纯对话）
   * ----------------------------------------------------------------------
   * 保持 v1 签名不变，老代码零改动即可继续用。
   * 新代码建议直接用 connect()。
   * ==================================================================== */

  async function createClient(options) {
    var opts = options || {}
    var bridge = await connect({
      baseUrl: opts.baseUrl,
      appId: opts.appId || 'anonymous',
      appName: opts.appName,
      token: opts.token,
      headers: opts.headers,
      fetch: opts.fetch,
      sessionId: opts.sessionId,
      storageKey: opts.storageKey,
      chat: true,
    })
    return {
      version: VERSION,
      get baseUrl() {
        return bridge.baseUrl
      },
      set baseUrl(v) {
        bridge.baseUrl = v
      },
      get sessionId() {
        return bridge.sessionId
      },
      set sessionId(v) {
        bridge.sessionId = v
      },
      chat: function (text, o) {
        return bridge.chat(text, o)
      },
      send: function (text, o) {
        return bridge.send(text, o)
      },
      abort: bridge.abort,
      health: bridge.health,
      tools: bridge.tools,
      approve: bridge.approve,
      reset: bridge.reset,
      on: bridge.on,
      disconnect: bridge.disconnect,
      bridge: bridge,
    }
  }

  /* ======================================================================
   * 七、轻量对话窗口（可选，挂在 bridge 之上）
   * ----------------------------------------------------------------------
   * 与 v1 的 mount 相比：多了「能力过程」展示（tool-invoke / 执行结果），
   * 因为 v2 的工具是在浏览器本地执行的，用户能看到「应用正在被操作」。
   * ==================================================================== */

  var STYLE_ID = 'harness-bridge-style'

  var CSS = [
    '.hbr-root{--hbr-bg:#fff;--hbr-fg:#1f1f1e;--hbr-muted:#6b6b68;--hbr-line:#e3e2de;',
    '--hbr-bot:#f5f5f3;--hbr-user:#1f1f1e;--hbr-userfg:#fff;--hbr-tool:#f0f6ea;--hbr-toolfg:#3b6d11;',
    '--hbr-warn:#fdf8ec;--hbr-warnline:#e4c27a;display:flex;flex-direction:column;height:100%;',
    'min-height:320px;background:var(--hbr-bg);color:var(--hbr-fg);border:1px solid var(--hbr-line);',
    'border-radius:12px;overflow:hidden;font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}',
    '.hbr-root[data-theme="dark"]{--hbr-bg:#1c1c1b;--hbr-fg:#ececea;--hbr-muted:#9a9a96;--hbr-line:#33332f;',
    '--hbr-bot:#282825;--hbr-user:#ececea;--hbr-userfg:#1c1c1b;--hbr-tool:#26301f;--hbr-toolfg:#a8d17a;',
    '--hbr-warn:#332c1c;--hbr-warnline:#7d6a3a}',
    '.hbr-head{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;',
    'border-bottom:1px solid var(--hbr-line);flex:0 0 auto}',
    '.hbr-title{font-weight:600;font-size:14px}',
    '.hbr-badge{font-size:11px;color:var(--hbr-muted);border:1px solid var(--hbr-line);border-radius:999px;padding:1px 8px;margin-left:8px}',
    '.hbr-ghost{background:none;border:1px solid var(--hbr-line);color:var(--hbr-muted);border-radius:8px;',
    'padding:4px 10px;font-size:12px;cursor:pointer;font-family:inherit}',
    '.hbr-ghost:hover{color:var(--hbr-fg)}',
    '.hbr-body{flex:1 1 auto;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}',
    '.hbr-row{display:flex;flex-direction:column;max-width:88%}',
    '.hbr-row.me{align-self:flex-end;align-items:flex-end}',
    '.hbr-bubble{padding:9px 13px;border-radius:12px;white-space:pre-wrap;word-break:break-word;background:var(--hbr-bot)}',
    '.hbr-row.me .hbr-bubble{background:var(--hbr-user);color:var(--hbr-userfg)}',
    '.hbr-sys{align-self:center;color:var(--hbr-muted);font-size:12px}',
    '.hbr-turn{display:flex;flex-direction:column;gap:8px;margin:0 0 16px}',
    '.hbr-steps{display:flex;flex-direction:column;gap:6px}',
    '.hbr-step{background:var(--hbr-tool);color:var(--hbr-toolfg);border-radius:8px;padding:7px 11px;font-size:12.5px;',
    'font-family:ui-monospace,Consolas,monospace;word-break:break-all;white-space:pre-wrap}',
    '.hbr-step.pending{opacity:.6}',
    '.hbr-answer{background:var(--hbr-bot);border-radius:12px;padding:11px 14px;white-space:pre-wrap;',
    'word-break:break-word;line-height:1.65;align-self:flex-start;max-width:100%}',
    '.hbr-approve{align-self:stretch;background:var(--hbr-warn);border:1px solid var(--hbr-warnline);border-radius:10px;padding:11px 13px}',
    '.hbr-approve .t{font-weight:600;font-size:13px;margin-bottom:4px}',
    '.hbr-approve .r{font-size:12.5px;color:var(--hbr-muted);margin-bottom:9px}',
    '.hbr-approve button{border:0;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;font-family:inherit;',
    'margin-right:8px;background:var(--hbr-user);color:var(--hbr-userfg)}',
    '.hbr-approve button.no{background:transparent;color:var(--hbr-fg);border:1px solid var(--hbr-line)}',
    '.hbr-foot{flex:0 0 auto;border-top:1px solid var(--hbr-line);padding:12px 16px;display:flex;gap:10px;align-items:flex-end}',
    '.hbr-input{flex:1 1 auto;resize:none;border:1px solid var(--hbr-line);background:transparent;color:var(--hbr-fg);',
    'border-radius:10px;padding:9px 12px;font:inherit;min-height:40px;max-height:140px;outline:none}',
    '.hbr-send{border:0;border-radius:10px;padding:10px 18px;font:inherit;font-weight:500;cursor:pointer;',
    'background:var(--hbr-user);color:var(--hbr-userfg);flex:0 0 auto}',
    '.hbr-send.stop{background:transparent;color:var(--hbr-fg);border:1px solid var(--hbr-line)}',
    '.hbr-cursor::after{content:"\\258C";opacity:.5;animation:hbr-blink 1s steps(1) infinite}',
    '@keyframes hbr-blink{50%{opacity:0}}',
  ].join('')

  function injectStyle(doc) {
    var d = doc || (typeof document !== 'undefined' ? document : null)
    if (!d || d.getElementById(STYLE_ID)) return
    var node = d.createElement('style')
    node.id = STYLE_ID
    node.textContent = CSS
    d.head.appendChild(node)
  }

  function makeEl(tag, className, text) {
    var node = document.createElement(tag)
    if (className) node.className = className
    if (text != null) node.textContent = text
    return node
  }

  function briefArgs(raw) {
    var parsed = typeof raw === 'string' ? safeJson(raw) : raw
    if (!parsed || typeof parsed !== 'object') return String(raw || '')
    var parts = []
    for (var k in parsed) {
      var v = parsed[k]
      var s = typeof v === 'string' ? v : JSON.stringify(v)
      if (s && s.length > 40) s = s.slice(0, 40) + '…'
      parts.push(k + ': ' + s)
      if (parts.length >= 3) break
    }
    return parts.join(', ')
  }

  function clip(text, max) {
    var s = String(text || '')
    return s.length > max ? s.slice(0, max) + '…' : s
  }

  /**
   * 挂一个对话窗口。options 会透传给 connect()，另外支持：
   *   title / greeting / placeholder / theme('auto'|'light'|'dark') / showTools / showReasoning
   *   onEvent(evt)
   */
  async function mount(target, options) {
    if (typeof document === 'undefined') throw new Error('[HarnessBridge] mount 只能在浏览器里使用')
    injectStyle()

    var opts = options || {}
    var host = typeof target === 'string' ? document.querySelector(target) : target
    if (!host) throw new Error('[HarnessBridge] 找不到挂载目标：' + target)

    var bridge = opts.bridge || (await connect(opts))
    var showTools = opts.showTools !== false
    var showReasoning = opts.showReasoning === true

    var root = makeEl('div', 'hbr-root')
    root.setAttribute('data-theme', opts.theme || 'auto')

    var head = makeEl('div', 'hbr-head')
    var titleWrap = makeEl('span')
    titleWrap.appendChild(makeEl('span', 'hbr-title', opts.title || 'AI 助手'))
    titleWrap.appendChild(makeEl('span', 'hbr-badge', bridge.appId + ' · ' + bridge.capabilities().length + ' 项能力'))
    head.appendChild(titleWrap)
    var actions = makeEl('div')
    var resetBtn = makeEl('button', 'hbr-ghost', '新对话')
    actions.appendChild(resetBtn)
    head.appendChild(actions)

    var body = makeEl('div', 'hbr-body')
    var foot = makeEl('div', 'hbr-foot')
    var input = makeEl('textarea', 'hbr-input')
    input.rows = 1
    input.placeholder = opts.placeholder || '说点什么…（Enter 发送，Shift+Enter 换行）'
    var sendBtn = makeEl('button', 'hbr-send', '发送')
    foot.appendChild(input)
    foot.appendChild(sendBtn)

    root.appendChild(head)
    root.appendChild(body)
    root.appendChild(foot)
    host.innerHTML = ''
    host.appendChild(root)

    function scrollToEnd() {
      body.scrollTop = body.scrollHeight
    }
    function addBubble(role, text) {
      var row = makeEl('div', 'hbr-row' + (role === 'me' ? ' me' : ''))
      row.appendChild(makeEl('div', 'hbr-bubble', text || ''))
      body.appendChild(row)
      scrollToEnd()
      return row
    }
    function addSystem(text) {
      body.appendChild(makeEl('div', 'hbr-sys', text))
      scrollToEnd()
    }

    var busy = false
    function setBusy(value) {
      busy = value
      sendBtn.textContent = value ? '停止' : '发送'
      sendBtn.className = value ? 'hbr-send stop' : 'hbr-send'
    }

    async function run(text) {
      addBubble('me', text)
      setBusy(true)

      var turnRoot = makeEl('div', 'hbr-turn')
      var stepsBox = makeEl('div', 'hbr-steps')
      var answerBox = null
      var pendingStep = null
      var pendingName = ''
      var pendingCallId = null
      var reasonBox = null

      body.appendChild(turnRoot)
      if (showTools) turnRoot.appendChild(stepsBox)
      scrollToEnd()

      function ensureAnswer() {
        if (!answerBox) {
          answerBox = makeEl('div', 'hbr-answer hbr-cursor')
          turnRoot.appendChild(answerBox)
          scrollToEnd()
        }
        return answerBox
      }
      function addStep(label, detail, pending) {
        if (!showTools) return null
        var step = makeEl('div', 'hbr-step' + (pending ? ' pending' : ''))
        step.appendChild(makeEl('div', 'st-name', label))
        if (detail) step.appendChild(makeEl('div', 'st-detail', detail))
        stepsBox.appendChild(step)
        scrollToEnd()
        return step
      }

      try {
        for await (var evt of bridge.chat(text)) {
          var data = evt.data || {}

          if (evt.type === 'session') continue

          if (evt.type === 'text-delta') {
            ensureAnswer().textContent += data.text || ''
            scrollToEnd()
            continue
          }

          if (evt.type === 'reasoning-delta') {
            if (showReasoning) {
              if (!reasonBox) reasonBox = addStep('思考过程', '', true)
              if (reasonBox) {
                var d = reasonBox.querySelector('.st-detail')
                if (!d) {
                  d = makeEl('div', 'st-detail')
                  reasonBox.appendChild(d)
                }
                d.textContent = clip(d.textContent + (data.text || ''), 400)
                scrollToEnd()
              }
            }
            continue
          }

          // 内核决定调工具（可能是本地能力，也可能是内核内置工具）
          if (evt.type === 'tool-call') {
            pendingName = String(data.name || '工具')
            pendingCallId = String(data.callId || '')
            pendingStep = addStep('→ 调用 ' + pendingName, briefArgs(data.arguments), true)
            continue
          }

          // ★ 本地能力被调用：显示「应用正在执行」
          if (evt.type === 'tool-invoke') {
            if (pendingStep) {
              pendingStep.appendChild(makeEl('div', 'st-detail', '在应用本地执行…'))
              scrollToEnd()
            }
            continue
          }

          if (evt.type === 'tool-result') {
            var resText = textOfMessage(data.message)
            var failed = Boolean(data.error)
            if (pendingStep) {
              pendingStep.className = 'hbr-step'
              pendingStep.textContent = ''
              pendingStep.appendChild(makeEl('div', 'st-name', (failed ? '✗ ' : '✓ ') + (pendingName || '工具')))
              if (resText) pendingStep.appendChild(makeEl('div', 'st-detail', clip(resText, 200)))
              pendingStep = null
              pendingName = ''
              pendingCallId = null
            } else if (showTools) {
              addStep((failed ? '✗ ' : '✓ ') + '工具', clip(resText, 200), false)
            }
            scrollToEnd()
            continue
          }

          if (evt.type === 'approval-request') {
            var isCap = data.approvalKind === 'capability'
            var card = makeEl('div', 'hbr-approve')
            card.appendChild(
              makeEl(
                'div',
                't',
                isCap
                  ? '应用能力请求授权：' + (data.capability || data.toolName || '未知能力')
                  : '工具请求授权：' + (data.toolName || '未知工具'),
              ),
            )
            if (isCap) {
              var riskLabel =
                data.risk === 'destructive' ? '会破坏数据' : data.risk === 'write' ? '会修改数据' : '只读'
              card.appendChild(
                makeEl(
                  'div',
                  'r',
                  '风险等级：' + riskLabel + (data.riskSource === 'untrusted-app' ? '（宿主推定，非应用自报）' : ''),
                ),
              )
              if (data.arguments) {
                card.appendChild(makeEl('div', 'r', '参数：' + clip(JSON.stringify(data.arguments), 160)))
              }
            }
            card.appendChild(makeEl('div', 'r', data.reason || '内核未给出原因'))

            var decide = function (decision, label) {
              bridge.approve(data.id, decision)
              card.innerHTML = ''
              card.appendChild(makeEl('div', 't', label))
            }
            var yes = makeEl('button', '', '允许一次')
            yes.onclick = function () {
              decide('allow-once', '已允许一次')
            }
            card.appendChild(yes)

            // 「总是允许」只在宿主声明支持时给出，避免按了没效果
            var features = (bridge.host && bridge.host.features) || null
            if (isCap && features && features.approvalAlways) {
              var always = makeEl('button', '', '本会话总是允许')
              always.onclick = function () {
                decide('allow-always', '本会话内已总是允许')
              }
              card.appendChild(always)
            }

            var no = makeEl('button', 'no', '拒绝')
            no.onclick = function () {
              decide('deny', '已拒绝')
            }
            card.appendChild(no)

            turnRoot.appendChild(card)
            scrollToEnd()
            continue
          }

          if (evt.type === 'assistant-message') {
            var full = textOfMessage(data.message)
            if (full) ensureAnswer().textContent = full
            continue
          }

          if (evt.type === 'turn-end') {
            if (answerBox) answerBox.classList.remove('hbr-cursor')
            var reason = data.reason
            if (reason && reason.kind === 'error') {
              addSystem('出错了：' + ((reason.error && reason.error.message) || '未知错误'))
            }
            continue
          }

          if (evt.type === 'error') {
            addSystem('错误：' + (data.message || '未知错误'))
            continue
          }
        }
      } finally {
        if (answerBox) answerBox.classList.remove('hbr-cursor')
        if (pendingStep) pendingStep.classList.remove('pending')
        setBusy(false)
      }
    }

    function submit() {
      var text = input.value.trim()
      if (!text) return
      input.value = ''
      input.style.height = 'auto'
      void run(text)
    }

    sendBtn.onclick = function () {
      if (busy) {
        bridge.abort()
        addSystem('已请求停止')
        return
      }
      submit()
    }
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        submit()
      }
    })
    input.addEventListener('input', function () {
      input.style.height = 'auto'
      input.style.height = Math.min(input.scrollHeight, 140) + 'px'
    })
    resetBtn.onclick = function () {
      bridge.reset()
      body.innerHTML = ''
      if (opts.greeting) addBubble('bot', opts.greeting)
      addSystem('已开始新对话')
    }

    if (opts.greeting) addBubble('bot', opts.greeting)
    // onEvent 已在 connect() 里统一转发，这里不再重复订阅

    return {
      el: root,
      bridge: bridge,
      send: function (text) {
        return run(text)
      },
      reset: function () {
        bridge.reset()
        body.innerHTML = ''
      },
      destroy: function () {
        bridge.abort()
        root.remove()
      },
    }
  }

  return {
    version: VERSION,
    protocol: PROTOCOL,
    compatibleProtocols: COMPATIBLE_PROTOCOLS.slice(),
    connect: connect,
    defineCapability: defineCapability,
    /* 声明式能力：从 <form> 派生 schema，零 JS 接入（P2-6） */
    fromForm: fromForm,
    createClient: createClient,
    mount: mount,
    /* 错误分类（对齐 MCP 的协议错误 / 工具执行错误） */
    executionError: executionError,
    protocolError: protocolError,
    /* 内容块构造助手（配合 output.format: 'blocks'） */
    textBlock: textBlock,
    imageBlock: imageBlock,
    audioBlock: audioBlock,
    linkBlock: linkBlock,
  }
})
