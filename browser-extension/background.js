/**
 * background.js —— service worker，负责把采集数据送到本地后端。
 *
 * ★ 为什么必须有它（这是「点了没反应」的真实原因）：
 *   内容脚本运行在**页面上下文**里，它发出的 fetch 会被**页面的 CSP**
 *   拦截。小红书/抖音这类站点的 CSP 普遍写得很严
 *   （connect-src 通常只允许 'self'），所以内容脚本直接
 *   fetch('http://127.0.0.1:8000') 会被浏览器静默拒绝——
 *   **不抛异常、也不发请求**，表现就是「点了没反应」。
 *
 *   service worker 运行在**扩展自身的源**下，不受页面 CSP 约束，
 *   所以跨源请求必须走这一层转发。
 *
 *   这是浏览器扩展的标准做法：内容脚本 → background → 目标服务。
 */

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== 'COLLECT') return false

  ;(async () => {
    try {
      const r = await fetch(msg.api, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(msg.payload),
      })
      const text = await r.text()
      let data
      try {
        data = JSON.parse(text)
      } catch {
        data = { detail: text.slice(0, 200) }
      }
      sendResponse({ ok: r.ok, status: r.status, data })
    } catch (err) {
      sendResponse({
        ok: false,
        status: 0,
        data: { detail: `后端连不上：${err?.message ?? err}` },
      })
    }
  })()

  // 异步响应必须 return true，否则消息通道会提前关闭
  return true
})
