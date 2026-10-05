/** popup.js —— 看采集状态与最近采集。 */
const API = 'http://127.0.0.1:8000'

function el(id) { return document.getElementById(id) }

async function refresh() {
  try {
    const [st, recent] = await Promise.all([
      fetch(`${API}/api/browser/status`).then((r) => r.json()),
      fetch(`${API}/api/browser/recent?limit=8`).then((r) => r.json()),
    ])
    el('cnt').textContent = st.collected ?? 0
    el('conn').innerHTML = '<span class="ok">● 后端已连接</span>'

    const list = el('list')
    if (!recent.items || recent.items.length === 0) {
      list.innerHTML = '<div class="item" style="color:#a1a1aa">还没有采集记录</div>'
      return
    }
    list.innerHTML = recent.items
      .map((i) => {
        const m = i.metrics || {}
        const bits = [
          m.likes != null ? `赞${m.likes}` : '',
          m.collects != null ? `藏${m.collects}` : '',
          m.comments != null ? `评${m.comments}` : '',
        ].filter(Boolean).join(' · ')
        return `<div class="item">
          ${escapeHtml(i.title)}
          <div class="m">${escapeHtml(i.source || '')} ${bits}</div>
        </div>`
      })
      .join('')
  } catch (e) {
    el('cnt').textContent = '–'
    el('conn').innerHTML = '<span class="err">● 后端未启动</span>'
    el('list').innerHTML =
      '<div class="item" style="color:#a1a1aa">请先启动后端（8000 端口）</div>'
  }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

el('open').addEventListener('click', () => {
  chrome.tabs.create({ url: 'http://127.0.0.1:5173' })
})

refresh()
