"""RSS 源管理 API。

让用户能在设置界面里增删改 RSS 源，不必手改 feeds.json。

设计要点：
  - 读操作走内存缓存，写操作同步落盘（保留 json 文件作为唯一数据源）
  - 每次写入都做基本校验，避免坏配置导致采集全挂
  - 提供连通性测试，选源时能立刻知道能不能用
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/api/feeds", tags=["feeds"])

FEEDS_FILE = Path(settings.data_dir) / "feeds.json"


# ── 模型 ────────────────────────────────────────────────
class FeedIn(BaseModel):
    """单个 RSS 源。"""

    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=8, max_length=1000)
    category: str = Field("综合", max_length=50)


class GroupIn(BaseModel):
    """一个赛道分组。"""

    key: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(..., min_length=1, max_length=100)
    desc: str = Field("", max_length=200)
    feeds: list[FeedIn] = Field(default_factory=list)


class FeedsUpdate(BaseModel):
    """整体覆盖更新（前端编辑后一次提交）。"""

    presets: list[GroupIn]


# ── 读写 ────────────────────────────────────────────────
def _read() -> dict[str, Any]:
    if not FEEDS_FILE.exists():
        return {"presets": []}
    try:
        with FEEDS_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"feeds.json 读取失败：{exc}"
        ) from exc


def _write(data: dict[str, Any]) -> None:
    """写回文件（presets 统一写成 list）。"""
    FEEDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["presets"] = _only_groups(data)
    tmp = FEEDS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(FEEDS_FILE)


def _validate_url(url: str) -> str:
    """校验 URL 基本合法性。"""
    u = url.strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail=f"URL 必须以 http/https 开头：{u}")
    if not p.netloc:
        raise HTTPException(status_code=400, detail=f"URL 缺少域名：{u}")
    return u


# ── 接口 ────────────────────────────────────────────────
@router.get("", summary="读取全部RSS 配置")
def list_feeds() -> dict[str, Any]:
    """返回所有赛道分组与其源。

    ★ 必须过滤掉 `_` 开头的说明字段（feeds.json 里有_说明/_用法 等字符串），
      否则会被当成分组处理而报错。
    """
    data = _read()
    presets = _only_groups(data)
    total = sum(len(p.get("feeds", [])) for p in presets)
    return {
        "presets": presets,
        "group_count": len(presets),
        "feed_count": total,
        "file_exists": FEEDS_FILE.exists(),
    }


def _only_groups(data: dict[str, Any]) -> list[dict[str, Any]]:
    """提取真正的分组，滤掉说明字段与坏数据。

    ★ feeds.json 里 presets 可能是 **dict**（key→分组配置）也可能是 **list**，
      两种都要兼容 —— 我们手写的文件是 dict，别的工具可能存list。
      同时要滤掉 `_` 开头的说明键（如 _失效源备查 也是 dict 但不是分组）。
    """
    raw = data.get("presets")

    if isinstance(raw, dict):
        items: list[Any] = [
            {**v, "key": k}
            for k, v in raw.items()
            if isinstance(v, dict) and not str(k).startswith("_")
        ]
    elif isinstance(raw, list):
        items = list(raw)
    else:
        items = []

    out: list[dict[str, Any]] = []
    for p in items:
        if not isinstance(p, dict):
            continue
        key = p.get("key")
        if not key or str(key).startswith("_"):
            continue
        feeds = [
            f
            for f in (p.get("feeds") or [])
            if isinstance(f, dict) and f.get("url")
        ]
        out.append(
            {
                "key": key,
                "label": p.get("label") or str(key),
                "desc": p.get("desc") or "",
                "feeds": feeds,
            }
        )
    return out


@router.put("", summary="整体覆盖 RSS 配置")
def save_feeds(payload: FeedsUpdate) -> dict[str, Any]:
    """前端编辑后一次性提交全部配置。"""
    presets: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for g in payload.presets:
        if g.key in seen_keys:
            raise HTTPException(
                status_code=400, detail=f"赛道 key 重复：{g.key}"
            )
        seen_keys.add(g.key)

        feeds = []
        seen_urls: set[str] = set()
        for fd in g.feeds:
            url = _validate_url(fd.url)
            if url in seen_urls:
                continue  # 组内去重，不报错
            seen_urls.add(url)
            feeds.append(
                {
                    "name": fd.name.strip(),
                    "url": url,
                    "category": fd.category.strip() or "综合",
                }
            )

        presets.append(
            {
                "key": g.key,
                "label": g.label.strip(),
                "desc": g.desc.strip(),
                "feeds": feeds,
            }
        )

    # 保留原文件里的说明字段（_开头），只替换 presets
    old = _read()
    notes = {k: v for k, v in old.items() if str(k).startswith("_") and k != "presets"}
    data = {
        **notes,
        "_说明": "RSS 源配置。可在工作台「设置 → RSS 源」里编辑，或直接改本文件。",
        "_用法": "流水线页选赛道预设后采集；单源失败会自动跳过不影响整体。",
        "presets": presets,   # 统一写成 list，结构更规范
    }
    _write(data)

    total = sum(len(p["feeds"]) for p in presets)
    return {
        "ok": True,
        "group_count": len(presets),
        "feed_count": total,
    }


@router.post("/group", summary="新增赛道分组")
def add_group(g: GroupIn) -> dict[str, Any]:
    """加一个新的赛道分组。"""
    data = _read()
    presets = _only_groups(data)
    if any(p.get("key") == g.key for p in presets):
        raise HTTPException(status_code=400, detail=f"赛道 key 已存在：{g.key}")

    presets.append(
        {
            "key": g.key,
            "label": g.label.strip(),
            "desc": g.desc.strip(),
            "feeds": [
                {
                    "name": f.name.strip(),
                    "url": _validate_url(f.url),
                    "category": f.category.strip() or "综合",
                }
                for f in g.feeds
            ],
        }
    )
    # ★ 关键：必须把改过的 presets 写回 data，否则 _write(data) 拿到的是
    #   _read() 的原始内容（presets 可能是 dict），新增的组会丢。
    data["presets"] = presets
    _write(data)
    return {"ok": True, "key": g.key}


@router.delete("/group/{key}", summary="删除赛道分组")
def delete_group(key: str) -> dict[str, Any]:
    """删掉一个赛道分组。"""
    data = _read()
    presets = _only_groups(data)
    before = len(presets)
    presets = [p for p in presets if p.get("key") != key]
    if len(presets) == before:
        raise HTTPException(status_code=404, detail=f"赛道不存在：{key}")
    data["presets"] = presets
    _write(data)
    return {"ok": True, "key": key}


@router.post("/probe", summary="测试单个源是否可用")
async def probe(body: dict[str, Any]) -> dict[str, Any]:
    """测试一个 RSS 源能否抓到内容。

    用真实请求验证——避免用户加了一堆失效源却不知道。
    """
    import httpx

    url = _validate_url(str(body.get("url", "")))

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; WorkbenchBot/0.1; +https://local)"
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "url": url,
            "reason": f"连接失败：{str(exc)[:80]}",
        }

    if resp.status_code != 200:
        return {
            "ok": False,
            "url": url,
            "reason": f"HTTP {resp.status_code}",
        }

    # 粗判是否含条目
    import feedparser

    parsed = feedparser.parse(resp.content)
    count = len(parsed.entries)
    if count == 0:
        return {
            "ok": False,
            "url": url,
            "reason": "能访问但没解析到条目（可能是网页而非 RSS）",
            "count": 0,
        }

    first = parsed.entries[0]
    return {
        "ok": True,
        "url": url,
        "count": count,
        "first_title": (first.get("title") or "")[:60],
    }
