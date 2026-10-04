"""RSS 采集服务（M1 采集层 · 源一）。

设计要点：
- 零合规风险：只订阅公开 RSS 源，不触碰小红书数据
- 内容去重：content_hash 避免重复入库
- 失败隔离：单个源失败不影响整体

依据 docs/06-采集与发布方案修正v2.md
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from urllib.request import Request, urlopen

import feedparser

from app.core.config import settings
from app.db.models import RawMaterial, SourceType

# User-Agent：部分源会拒绝空 UA
UA = "Mozilla/5.0 (compatible; WorkbenchBot/0.1; +https://local)"

# RSS 源配置文件 —— 换赛道只改这个 json，不用改代码
FEEDS_FILE = settings.data_dir / "feeds.json"

# 文件缺失/损坏时的兜底，保证采集不因配置问题挂掉
_FALLBACK_PRESETS = {
    "tech": {
        "label": "科技 / AI",
        "desc": "数码工具、AI 应用",
        "feeds": [
            {"name": "InfoQ中文", "url": "https://www.infoq.cn/feed", "category": "技术"},
            {"name": "36氪", "url": "https://36kr.com/feed", "category": "科技商业"},
        ],
    }
}


@dataclass
class FeedConfig:
    """RSS 源配置。"""

    name: str
    url: str
    category: str = "综合"


def load_presets() -> dict:
    """读取 feeds.json 里的赛道预设。"""
    try:
        if not FEEDS_FILE.exists():
            return _FALLBACK_PRESETS
        with FEEDS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        return data.get("presets") or _FALLBACK_PRESETS
    except Exception as exc:  # noqa: BLE001
        print(f"[rss] 读取 {FEEDS_FILE} 失败，用兜底配置: {exc}")
        return _FALLBACK_PRESETS


def list_presets() -> list[dict]:
    """列出所有赛道预设（供前端下拉选择）。"""
    return [
        {
            "key": key,
            "label": conf.get("label", key),
            "desc": conf.get("desc", ""),
            "count": len(conf.get("feeds", [])),
        }
        for key, conf in load_presets().items()
    ]


def feeds_of_preset(preset_key: str) -> list[FeedConfig]:
    """取某个预设的源列表。"""
    conf = load_presets().get(preset_key) or {}
    return [
        FeedConfig(
            name=f.get("name", "未命名"),
            url=f.get("url", ""),
            category=f.get("category", "综合"),
        )
        for f in conf.get("feeds", [])
        if f.get("url")
    ]


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
    preset: str | None = None,
) -> dict:
    """批量采集并入库（去重）。

    参数：
      feeds  显式指定的源；不给则用 preset
      preset 赛道预设 key（见 feeds.json）；都不给则用第一个预设
    """
    if feeds is None and preset:
        feeds = feeds_of_preset(preset)
    if not feeds:
        # 兜底：取第一个预设
        presets = load_presets()
        if presets:
            first = next(iter(presets))
            feeds = feeds_of_preset(first)
            preset = preset or first

    added = 0
    duplicated = 0
    per_feed: list[dict] = []

    for feed in feeds or []:
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
        "preset": preset,
        "fetched": added + duplicated,
        "added": added,
        "duplicated": duplicated,
        "per_feed": per_feed,
    }
