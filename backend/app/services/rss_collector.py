"""RSS 采集服务（M1 采集层 · 源一）。

设计要点：
- 零合规风险：只订阅公开 RSS 源，不触碰小红书数据
- 内容去重：content_hash 避免重复入库
- 失败隔离：单个源失败不影响整体

依据 docs/06-采集与发布方案修正v2.md
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from urllib.request import Request, urlopen

import feedparser

from app.db.models import RawMaterial, SourceType

# User-Agent：部分源会拒绝空 UA
UA = "Mozilla/5.0 (compatible; WorkbenchBot/0.1; +https://local)"


@dataclass
class FeedConfig:
    """RSS 源配置。"""

    name: str
    url: str
    category: str = "综合"


# 默认源清单：可按赛道调整（docs/08 待确认项 #2）
DEFAULT_FEEDS: list[FeedConfig] = [
    FeedConfig("机器之心", "https://www.jiqizhixin.com/rss", "科技AI"),
    FeedConfig("36氪", "https://36kr.com/feed", "科技商业"),
    FeedConfig("虎嗅", "https://www.huxiu.com/rss/0.xml", "商业"),
    FeedConfig("InfoQ中文", "https://www.infoq.cn/feed", "技术"),
]


def _hash(title: str, url: str) -> str:
    """内容指纹：标题+链接的哈希，用于去重。"""
    raw = f"{title.strip()}|{url.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _parse_time(entry) -> datetime | None:
    """解析 RSS 时间字段，失败返回 None。"""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return datetime(*struct[:6])
            except (TypeError, ValueError):
                continue
    return None


def fetch_feed(feed: FeedConfig, limit: int = 10) -> list[dict]:
    """抓取单个 RSS 源，返回条目列表。失败返回空列表。"""
    try:
        req = Request(feed.url, headers={"User-Agent": UA})
        with urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - 单源失败不影响整体
        print(f"[rss] 源 {feed.name} 抓取失败: {exc}")
        return []

    parsed = feedparser.parse(raw)
    if not parsed.entries:
        print(f"[rss] 源 {feed.name} 无条目")
        return []

    items: list[dict] = []
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        summary = entry.get("summary") or ""
        # 粗清洗：去掉 HTML 标签，压缩空白
        summary = _strip_html(summary)[:2000]

        items.append(
            {
                "source_type": SourceType.RSS,
                "source_url": link,
                "source_name": feed.name,
                "title": title,
                "raw_content": summary,
                "summary": summary[:500],
                "author": (entry.get("author") or "")[:200],
                "published_at": _parse_time(entry),
                "content_hash": _hash(title, link),
                # RSS 源非自有账号内容，原创风险低（但仍需过合规筛）
                "own_flag": 0,
                "originality_risk": "low",
            }
        )
    return items


def _strip_html(text: str) -> str:
    """去 HTML 标签（够用即可，不引额外依赖）。"""
    import re

    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"&nbsp;?", " ", cleaned)
    cleaned = re.sub(r"&amp;?", "&", cleaned)
    cleaned = re.sub(r"&lt;?", "<", cleaned)
    cleaned = re.sub(r"&gt;?", ">", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def collect_rss(
    db,
    feeds: list[FeedConfig] | None = None,
    limit_per_feed: int = 10,
) -> dict:
    """批量采集并入库（去重）。

    返回统计：抓取条目数、新增数、重复数、各源状态。
    """
    feeds = feeds or DEFAULT_FEEDS
    added = 0
    duplicated = 0
    per_feed: list[dict] = []

    for feed in feeds:
        items = fetch_feed(feed, limit=limit_per_feed)
        feed_added = 0
        for item in items:
            exists = (
                db.query(RawMaterial)
                .filter(RawMaterial.content_hash == item["content_hash"])
                .first()
            )
            if exists:
                duplicated += 1
                continue
            db.add(RawMaterial(**item))
            feed_added += 1
        db.commit()
        added += feed_added
        per_feed.append(
            {"name": feed.name, "fetched": len(items), "added": feed_added}
        )
        print(f"[rss] {feed.name}: 抓取 {len(items)} 条，新增 {feed_added} 条")

    return {
        "fetched": added + duplicated,
        "added": added,
        "duplicated": duplicated,
        "per_feed": per_feed,
    }
