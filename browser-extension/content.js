/**
 * content.js —— 在页面上注入采集按钮 + 提取当前内容。
 *
 * ★ 合规边界（这个扩展严格遵守，不要绕过）：
 *   1. **只在你点击时采集**——没有任何定时器、轮询、自动翻页
 *   2. **只读 DOM 已经渲染出来的内容**——不解析 X-Bogus 等反爬参数，
 *      不发任何绕过验证的请求，等价于「你眼睛看到什么就记什么」
 *   3. **不采用户可识别信息**——不要评论者昵称/头像/用户ID
 *      （后端也会再拦一次，双保险）
 *   4. 遇到验证码/登录墙就停手，不尝试绕过
 *
 * 换句话说：**这是人工浏览的辅助记录工具，不是爬虫。**
 */

const API = 'http://127.0.0.1:8000/api/browser/collect'
const FLOAT_ID = 'wb-collect-float'

/** 平台识别 */
function detectPlatform(host) {
  const h = (host || '').toLowerCase()
  if (h.includes('xiaohongshu') || h.includes('xhslink')) return '小红书'
  if (h.includes('douyin')) return '抖音'
  if (h.includes('weibo')) return '微博'
  if (h.includes('zhihu')) return '知乎'
  return null
}

/** 页面可能出现的登录/验证墙——遇到就提示用户，别硬来 */
function blockedBy() {
  const t = document.title || ''
  const url = location.href || ''
  if (/验证|安全检查|滑块|captcha|人机/.test(t)) return '页面要求安全验证'
  if (/login|passport/.test(url) && !/explore|note|video/.test(url)) {
    return '看起来跳到了登录页'
  }
  return null
}

/** 通用：按多个候选选择器取文本，取不到返回空 */
function pickText(selectors, root = document) {
  for (const sel of selectors) {
    const el = root.querySelector(sel)
    const t = el?.textContent?.trim()
    if (t) return t
  }
  return ''
}

/** 解析「1.2万」这类数字 */
function parseCount(s) {
  if (!s) return null
  const t = String(s).replace(/\s/g, '')
  const m = t.match(/([\d.]+)\s*([wW万kK千]?)/)
  if (!m) return null
  let n = parseFloat(m[1])
  const unit = m[2]
  if (unit === 'w' || unit === 'W' || unit === '万') n *= 10000
  if (unit === 'k' || unit === 'K' || unit === '千') n *= 1000
  return Number.isFinite(n) ? Math.round(n) : null
}

/** 提取当前页面的内容 */
function extract() {
  const host = location.hostname
  const platform = detectPlatform(host)
  if (!platform) return { ok: false, error: '当前站点不在支持列表' }

  const block = blockedBy()
  if (block) {
    return { ok: false, error: `${block}。扩展不会尝试绕过，请先手动完成验证。` }
  }

  let title = ''
  let content = ''
  let topics = []
  let author = ''
  let noteType = ''
  const metrics = {}

  if (platform === '小红书') {
    // 详情页：#detail-title / .title 是标题，#detail-desc 是正文
    title = pickText([
      '#detail-title',
      'div.note-content .title',
      'span.note-content .title',
      'h1.title',
    ])
    content = pickText(['#detail-desc', 'div.note-content .desc', '.desc'])
    author = pickText(['.author-wrapper .username', '.author-name', '#user-name'])
    noteType = document.querySelector('.note-slider-video, video') ? '视频' : '图文'

    const likeEl = document.querySelector('#like-wrapper .count, .engage-bar .like-wrapper .count')
    const cmtEl = document.querySelector('#comment-count .count, .engage-bar .chat-wrapper .count')
    const colEl = document.querySelector('#collect-wrapper .count, .engage-bar .collect-wrapper .count')
    metrics.likes = parseCount(likeEl?.textContent)
    metrics.comments = parseCount(cmtEl?.textContent)
    metrics.collects = parseCount(colEl?.textContent)

    topics = [...document.querySelectorAll('.topic-tag, a.tag, .note-content .tag')
      .map((e) => e.textContent?.trim())
      .filter((t) => t && t.length < 24)].slice(0, 10)
  } else if (platform === '抖音') {
    title = pickText(['h1[data-e2e]', 'div.video-title h1', 'h1._1gtRw31'])
    content = pickText(['div.video-info-detail', 'div[data-e2e="video-desc"]'])
    author = pickText(['span[data-e2e="video-author-uniqueid"]', '.author-name h1'])
    noteType = '视频'
    topics = [...document.querySelectorAll('.video-tag, .hashtag, a[href*="/hashtag/"]')
      .map((e) => e.textContent?.trim())
      .filter((t) => t && t.length < 24)].slice(0, 10)
    const m = pickText(['.video-like-count', '[data-e2e="video-like-count"]'])
    metrics.likes = parseCount(m)
  } else {
    // 其他平台：通用兜底
    title = pickText(['h1', 'article h1', 'meta[property="og:title"]']) ||
      document.querySelector('meta[property="og:title"]')?.content || document.title
    content = pickText(['article', 'meta[property="og:description"]']) ||
      document.querySelector('meta[name="description"]')?.content || ''
    author = pickText(['[rel="author"]', '.author', '.author-name'])
    noteType = '网页'
  }

  if (!title || !title.trim()) {
    return { ok: false, error: '没识别到标题。可能页面还没加载完，或不是内容详情页。' }
  }

  return {
    ok: true,
    payload: {
      url: location.href.slice(0, 1000),
      title: title.trim().slice(0, 500),
      content: (content || '').trim().slice(0, 20000),
      topics: [...new Set(topics)].slice(0, 10),
      author_display: (author || '').trim().slice(0, 200),
      note_type: noteType,
      likes: metrics.likes ?? null,
      collects: metrics.collects ?? null,
      comments: metrics.comments ?? null,
      shares: null,
      keyword: '',
    },
    platform,
  }
}

/** 提示条 */
let toastTimer = null
function toast(msg, kind = 'ok') {
  const old = document.getElementById('wb-toast')
  if (old) old.remove()
  const el = document.createElement('div')
  el.id = 'wb-toast'
  el.textContent = msg
  el.className = `wb-toast wb-toast-${kind}`
  document.body.appendChild(el)
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => el.remove(), 3800)
}

/** 浮层按钮 */
function injectButton() {
  if (document.getElementById(FLOAT_ID)) return
  if (!detectPlatform(location.hostname)) return

  const wrap = document.createElement('div')
  wrap.id = FLOAT_ID
  wrap.innerHTML = `
    <button class="wb-btn" title="把当前内容存进工作台需求库">
      <span class="wb-btn-ico">＋</span><span>采集</span>
    </button>
  `
  document.body.appendChild(wrap)

  wrap.querySelector('.wb-btn').addEventListener('click', async (e) => {
    e.preventDefault()
    const btn = wrap.querySelector('.wb-btn')
    const res = extract()

    if (!res.ok) {
      toast(res.error, 'err')
      return
    }

    btn.classList.add('wb-btn-busy')
    const old = btn.innerHTML
    btn.innerHTML = '<span class="wb-btn-ico">…</span><span>存入中</span>'

    try {
      const r = await fetch(API, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(res.payload),
      })
      const d = await r.json()
      if (!r.ok) {
        toast(d.detail || '采集失败', 'err')
      } else {
        const total = d.metrics?.total
        toast(
          d.action === 'updated'
            ? `已更新素材 #${d.id}`
            : `已存入素材 #${d.id}${total ? `（互动 ${total}）` : ''}`,
          'ok'
        )
      }
    } catch (err) {
      toast(`连不上工作台后端（${err.message}）。请确认 8000 端口已启动。`, 'err')
    } finally {
      btn.classList.remove('wb-btn-busy')
      btn.innerHTML = old
    }
  })
}

//单页应用路由变化时重新注入
let lastUrl = location.href
const observer = new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href
    injectButton()
  }
})
observer.observe(document, { subtree: true, childList: true })

injectButton()
