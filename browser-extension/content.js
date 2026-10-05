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
// 提示条与按钮都放在 Shadow DOM 里，页面 CSS 管不到，
// 所以不再需要全局 id。

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

/** 取属性值（如 meta 标签的 content、video 的 poster） */
function pickAttr(selector, attr) {
  const el = document.querySelector(selector)
  const v = el?.getAttribute?.(attr)?.trim()
  return v || ''
}

/** 取第一个非空图片的 src（用于封面兜底） */
function pickFirstImgSrc(selectors) {
  for (const sel of selectors) {
    const el = document.querySelector(sel)
    if (!el) continue

    // ★ srcset 优先：常含多档清晰度，取最大的一档。
    //   小红书/抖音的图是懒加载的，src 常是 base64 占位图（1x1透明），
    //   真实地址在 data-src / srcset 上 —— 只读 src 就只能拿到占位图，
    //   这正是「采集不到图」的原因。
    const srcset =
      el.getAttribute?.('srcset') || el.getAttribute?.('data-srcset') || ''
    if (srcset) {
      const best = srcset
        .split(',')
        .map((s) => s.trim().split(/\s+/))
        .filter((p) => p[0] && !p[0].startsWith('data:'))
        .sort((a, b) => {
          const w = (p) => (p[1] && /w$/.test(p[1]) ? parseInt(p[1]) : 0)
          return (w(b) || b[0].length) - (w(a) || a[0].length)
        })[0]
      if (best?.[0]) return best[0]
    }

    const src =
      el.getAttribute?.('data-src') ||
      el.getAttribute?.('data-original') ||
      el.getAttribute?.('src') ||
      (el.tagName === 'IMG' ? el.src : '')
    // 过滤 base64 占位图：长度很短的一定是占位
    if (src && !src.startsWith('data:') && src.length > 40) return src
  }
  return ''
}

/** 兜底：找页面上**显示尺寸最大**的那张图。
 *
 * ★ 为什么需要：平台改版频繁，class 名一直在变，
 *   靠选择器迟早失效。「渲染宽度最大的图」是**视觉事实**，
 *   不依赖任何 class，比选择器稳得多。
 */
function pickLargestImage() {
  let best = ''
  let bestArea = 0
  const imgs = document.querySelectorAll('img')
  for (const img of imgs) {
    const r = img.getBoundingClientRect?.()
    const area = (r?.width || 0) * (r?.height || 0)
    // 太小的是头像/图标；太大的是广告位，都不要
    if (area < 8000 || area > 2000000) continue
    const src =
      img.getAttribute('srcset') ||
      img.getAttribute('data-src') ||
      img.getAttribute('src') ||
      img.src ||
      ''
    const first = src.split(',')[0]?.trim().split(/\s+/)[0] || src
    if (first.startsWith('data:')) continue
    if (area > bestArea) {
      bestArea = area
      best = first
    }
  }
  return best
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

/** 提取当前页面的内容（含封面图） */
function extract() {
  const host = location.hostname
  const platform = detectPlatform(host)
  if (!platform) return { ok: false, error: '当前站点不在支持列表' }

  const block = blockedBy()
  if ( block) {
    return { ok: false, error: `${block}。扩展不会尝试绕过，请先手动完成验证。` }
  }

  let title = ''
  let content = ''
  let topics = []
  let author = ''
  let noteType = ''
  let cover = ''
  const metrics = {}

  // ── 封面图提取（通用，放最前面）──
  // ★ 采集的封面要转存到本机，不能直接存小红书的 CDN 地址：
  //   1) CDN 通常带防盗链签名，过期后 URL 就失效了
  //   2) 别人图片的 URL 留在库里，将来二次创作时拿不到图
  cover =
    pickAttr('meta[property="og:image"]', 'content') ||
    pickAttr('meta[name="twitter:image"]', 'content') ||
    pickFirstImgSrc([
      // 小红书：笔记大图（多个版本都试一遍，改版频繁）
      '.note-slider-img',
      '.swiper-slide img',
      '.note-detail-img',
      '.player-container img',
      '.cover img',
      '.img-container img',
      '.media-container img',
      '#noteContainer img',
      'article img',
      // 兜底：页面上尺寸最大的那张图（排除头像/图标）
      '',
    ]) ||
    pickLargestImage() ||
    ''
  if (cover && !cover.startsWith('http')) {
    cover = cover.startsWith('//') ? `https:${cover}` : new URL(cover, location.href).href
  }

  if (platform === '小红书') {
    // ★ 选择器要多备几套：小红书改版频繁，老选择器会失效
    title = pickText([
      '#detail-title',              // 老版
      '.note-content .title',       // 老版
      'h1#detail-title',
      '[class*="title"][class*="note"]',
      '.note-detail-mask .title',
      'h1.title',
      'div[class*="Title"] h1',
      'h1[class*="Title"]',
      'h1',
    ])
    content = pickText([
      '#detail-desc',
      '.note-content .desc',
      '.desc',
      '[class*="desc"][class*="note"]',
      'span[class*="desc"]',
    ])
    author = pickText([
      '.author-wrapper .username',
      '.author-name',
      '#user-name',
      '[class*="author"] [class*="name"]',
      '.author-container .name',
    ])
    noteType = document.querySelector('.note-slider-video, video') ? '视频' : '图文'

    const likeEl = document.querySelector(
      '#like-wrapper .count, .engage-bar .like-wrapper .count, [class*="like"][class*="count"]'
    )
    const cmtEl = document.querySelector(
      '#comment-count .count, .engage-bar .chat-wrapper .count, [class*="comment"][class*="count"]'
    )
    const colEl = document.querySelector(
      '#collect-wrapper .count, .engage-bar .collect-wrapper .count, [class*="collect"][class*="count"]'
    )
    metrics.likes = parseCount(likeEl?.textContent)
    metrics.comments = parseCount(cmtEl?.textContent)
    metrics.collects = parseCount(colEl?.textContent)

    topics = [
      ...document.querySelectorAll(
        '.topic-tag, a.tag, .note-content .tag, [class*="topic"], .hashtag'
      ),
    ]
      .map((e) => e.textContent?.trim())
      .filter((t) => t && t.length < 24)
      .slice(0, 10)
  } else if (platform === '抖音') {
    title = pickText([
      'h1[data-e2e]', 'div.video-title h1', 'h1._1gtRw31',
      '[class*="video-title"] h1', 'h1',
    ])
    content = pickText([
      'div.video-info-detail', 'div[data-e2e="video-desc"]', '[class*="video-info"]',
    ])
    author = pickText([
      'span[data-e2e="video-author-uniqueid"]', '.author-name h1', '[class*="author"] span',
    ])
    noteType = '视频'
    // 抖音封面常在 video 的 poster 或 cover 容器里
    cover =
      pickAttr('video', 'poster') || cover
    topics = [
      ...document.querySelectorAll('.video-tag, .hashtag, a[href*="/hashtag/"]'),
    ]
      .map((e) => e.textContent?.trim())
      .filter((t) => t && t.length < 24)
      .slice(0, 10)
    const m = pickText(['.video-like-count', '[data-e2e="video-like-count"]'])
    metrics.likes = parseCount(m)
  } else {
    const og = document.querySelector('meta[property="og:title"]')?.content || ''
    const desc = document.querySelector('meta[name="description"]')?.content || ''
    title = pickText(['h1', 'article h1']) || og || document.title
    content = pickText(['article']) || desc
    author = pickText(['[rel="author"]', '.author', '.author-name'])
    noteType = '网页'
  }

  if (!title || !title.trim()) {
    return {
      ok: false,
      error:
        '没识别到标题。可能是页面没加载完，或者不是内容详情页' +
        '（请点开一篇笔记再采集）。若持续失败，按 F12 看控制台有无扩展报错。',
    }
  }

  return {
    ok: true,
    payload: {
      url: location.href.slice(0, 1000),
      title: title.trim().slice(0, 500),
      content: (content || '').trim().slice(0, 20000),
      //★ 标题之外还要回传全文——此前只传了标题，正文丢失
      full_text: (document.body?.innerText || '').trim().slice(0, 30000),
      cover: (cover || '').slice(0, 1000),
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

/** 提示条
 *
 * ★ 用 Shadow DOM 隔离：页面样式可能给 `div` 加高 z-index、
 *   覆盖 position/字体等属性，导致 toast 看不见——表现同样是「没反应」。
 *   放进 shadow root 后页面 CSS 管不到。
 */
let toastHost = null
function getToastHost() {
  if (toastHost && toastHost.isConnected) return toastHost
  toastHost = document.createElement('div')
  toastHost.style.cssText = 'all:initial;position:fixed;z-index:2147483647;top:0;left:0;'
  const root = toastHost.attachShadow({ mode: 'open' })
  const style = document.createElement('style')
  style.textContent = `
    .t {
      position: fixed; left: 50%; top: 72px; transform: translateX(-50%);
      max-width: 480px; padding: 10px 18px; border-radius: 10px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
        'PingFang SC', 'Microsoft YaHei', sans-serif;
      font-size: 13px; line-height: 1.5; color: #fff;
      box-shadow: 0 8px 28px rgba(0,0,0,.18);
      animation: in .18s ease-out;
    }
    .ok { background: #10b981; }
    .err { background: #ef4444; }
    @keyframes in { from { opacity:0; transform:translateX(-50%) translateY(-6px); }
                    to { opacity:1; transform:translateX(-50%) translateY(0); } }
  `
  root.appendChild(style)
  document.body.appendChild(toastHost)
  return toastHost
}

let toastTimer = null
function toast(msg, kind = 'ok') {
  const host = getToastHost()
  const old = host.shadowRoot?.querySelector('.t')
  if (old) old.remove()

  const el = document.createElement('div')
  el.className = `t ${kind}`
  el.textContent = msg
  host.shadowRoot.appendChild(el)

  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => el.remove(), 4200)
}

/** 浮层按钮
 *
 * ★ 同样用 Shadow DOM 隔离。小红书这类页面会给全局元素加样式，
 *   直接插入的 div 可能被覆盖 z-index/position 而「看不见或点不到」。
 */
let buttonHost = null
function injectButton() {
  if (buttonHost && buttonHost.isConnected) return
  if (!detectPlatform(location.hostname)) return

  buttonHost = document.createElement('div')
  buttonHost.style.cssText = 'all:initial;position:static;'
  const root = buttonHost.attachShadow({ mode: 'open' })
  root.innerHTML = `
    <style>
      .f {
        position: fixed; right: 20px; bottom: 96px; z-index: 2147483646;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
          'PingFang SC', 'Microsoft YaHei', sans-serif;
      }
      .b {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 8px 14px; border: 1px solid rgba(124,58,237,.35);
        border-radius: 999px; background: rgba(124,58,237,.92); color: #fff;
        font-size: 13px; font-weight: 500; cursor: pointer;
        box-shadow: 0 4px 16px rgba(124,58,237,.3);
        transition: all .16s ease; user-select: none;
      }
      .b:hover { background: rgba(124,58,237,1); transform: translateY(-1px); }
      .b:active { transform: translateY(0); }
      .busy { opacity: .75; cursor: wait; }
    </style>
    <div class="f"><button class="b" title="把当前内容存进工作台需求库">
      <span>＋</span><span>采集</span></button></div>
  `
  document.body.appendChild(buttonHost)

  root.querySelector('.b').addEventListener('click', async (e) => {
    // ★ 必须 stopPropagation：页面的全局点击处理器（选中文本、关弹层等）
    //   可能把事件吃掉，导致看起来「点了没反应」。
    e.preventDefault()
    e.stopPropagation()

    const btn = root.querySelector('.b')
    const res = extract()

    if (!res.ok) {
      toast(res.error, 'err')
      return
    }

    btn.classList.add('busy')
    const old = btn.innerHTML
    btn.innerHTML = '<span>…</span><span>存入中</span>'

    try {
      // ★ 必须经 background 转发：页面 CSP 会拦截内容脚本的跨源请求
      //   且**不报错**，表现就是点了没反应。
      const reply = await chrome.runtime.sendMessage({
        type: 'COLLECT',
        api: API,
        payload: res.payload,
      })

      if (!reply) {
        toast('扩展后台没有响应。请到 edge://extensions/ 重载扩展。', 'err')
        return
      }
      if (!reply.ok) {
        toast(`采集失败：${reply.data?.detail || reply.status}`, 'err')
        return
      }

      const d = reply.data || {}
      const total = d.metrics?.total
      toast(
        d.action === 'updated'
          ? `已更新素材 #${d.id}`
          : `已存入素材 #${d.id}${total ? `（互动 ${total}）` : ''}`,
        'ok'
      )
    } catch (err) {
      toast(
        `采集异常：${err?.message ?? err}。若后端没启动，请先跑 8000 端口。`,
        'err'
      )
    } finally {
      btn.classList.remove('busy')
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
