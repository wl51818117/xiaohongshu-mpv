"""浏览器扩展采集接收端（2026-10-05）。

★ 合规边界（写在代码里，不只写在文档里）：
  这个端点只接受**用户主动点击**采集来的**单页公开内容**：
    1. 只取标题、正文、话题、互动数——**不取用户昵称/头像/评论者信息**
    2. 不接受批量提交（单次请求最多 1 条，见 _assert_single_item）
    3. 扩展不做自动翻页/定时轮询，由用户在页面上手动触发

  与爬虫的区别（也是判例区分侵入与否的关键）：
    爬虫 = 程序自动批量 + 突破技术防护
    这里 = 用户自己在浏览器里正常浏览，扩展只是把他看到的内容结构化

  参考判例：建湖「代拓客」案认定该软件「非法获取平台用于反爬虫的 X-Bogus
  安全防护参数」→ 判定为侵入。本扩展**不涉及任何反爬参数**，
  只读取 DOM 已在渲染的内容。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RawMaterial, SourceType
from app.db.session import get_db

router = APIRouter(prefix="/api/browser", tags=["browser"])

# 允许采集的平台（白名单，不在名单里的一律拒绝）
ALLOWED_HOSTS = {
    "xiaohongshu.com": "小红书",
    "www.xiaohongshu.com": "小红书",
    "xhslink.com": "小红书",
    "douyin.com": "抖音",
    "www.douyin.com": "抖音",
    "weibo.com": "微博",
    "zhihu.com": "知乎",
    "taobao.com": "淘宝",
    "jd.com": "京东",
}

# ★ 明确拒收的字段：可识别个人信息。
#   即使扩展传了，后端也丢掉——不依赖前端自觉。
FORBIDDEN_FIELDS = {
    "user_id", "userId", "nickname", "nickName", "avatar", "avatarUrl",
    "author_id", "authorId", "user_name", "userName", "commenter",
    "commenters", "user_list", "followers", "userIds",
}


class CollectIn(BaseModel):
    """扩展采来的单条内容。"""

    # ★ `extra="forbid"` 是**合规设计的核心**，不是可选的严格性。
    #   Pydantic 默认 extra="ignore" —— 多传的字段会被静默丢弃，
    #   于是 `_assert_safe` 永远看不到它们，合规拦截形同虚设。
    #   （实测踩过：传 userId/avatar/nickname 竟然被接收了）
    #   改成 forbid 后，扩展一旦传了可识别信息 → 直接 422。
    model_config = {"extra": "forbid"}

    # ── 必填：定位与归属 ──
    url: str = Field(..., description="当前页面 URL")
    title: str = Field(..., min_length=1, max_length=500)

    # ── 内容 ──
    content: str = Field("", description="正文（如果页面上有）")
    topics: list[str] = Field(default_factory=list, description="话题标签")
    author_display: str = Field(
        "", description="页面显示的博主昵称（公开信息，用于标注来源）"
    )

    # ── 互动数（只存数字，不存是谁点赞的）──
    likes: int | None = None
    collects: int | None = None
    comments: int | None = None
    shares: int | None = None

    # ── 归类辅助 ──
    note_type: str = Field("", description="扩展识别到的内容形态：笔记/视频/问答")
    keyword: str = Field("", description="用户顺手填的核心词（可选）")


def _assert_safe(payload: dict) -> None:
    """合规校验：拒收可识别个人信息。

    ★ 双重保险，缺一不可：
      1. `CollectIn.model_config = {"extra": "forbid"}`
         —— 让多余字段根本进不来（Pydantic 默认是ignore，会静默丢弃）
      2. 这里的显式检查 —— 语义更清楚，且将来若改成 allow 了还能兜住
    """
    leaked = FORBIDDEN_FIELDS & set(payload.keys())
    if leaked:
        raise HTTPException(
            status_code=400,
            detail=f"拒绝接收：包含用户可识别信息 {sorted(leaked)}。"
            "采集公开内容即可，不要传用户 ID/昵称/头像等字段",
        )


def _assert_allowed_host(url: str) -> tuple[str, str]:
    """校验域名在白名单内。返回 (host, 平台名)。"""
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"URL 无法解析：{exc}") from exc

    if not host:
        raise HTTPException(status_code=400, detail="URL 缺少主机名")

    platform = ALLOWED_HOSTS.get(host)
    if not platform:
        # 允许子域（如 explore.xiaohongshu.com）
        platform = next(
            (v for k, v in ALLOWED_HOSTS.items() if host.endswith(f".{k}")),
            None,
        )
    if not platform:
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持采集该站点（{host}）。"
            f"当前支持：{'、'.join(sorted(set(ALLOWED_HOSTS.values())))}",
        )
    return host, platform


def _fingerprint(url: str, title: str) -> str:
    """内容指纹，用于去重。"""
    raw = f"{url.strip()}|{title.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


@router.post("/collect", summary="接收浏览器扩展采集的单条内容")
def collect(payload: CollectIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """接收一条浏览器采集的内容。

    ★ 幂等：同 URL + 同标题重复采集会更新而非新增。
    """
    data = payload.model_dump()
    _assert_safe(data)
    _host, platform = _assert_allowed_host(payload.url)

    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="标题不能为空")

    # 互动数整理
    metrics: dict[str, int] = {}
    for k in ("likes", "collects", "comments", "shares"):
        v = getattr(payload, k)
        if v is not None and v >= 0:
            metrics[k] = v
    if metrics:
        # 互动总量便于排序（不是质量指标，只用于筛选参考）
        metrics["total"] = sum(metrics.values())

    # 话题清洗
    topics = []
    for t in payload.topics:
        t = re.sub(r"^#|#$", "", str(t)).strip()
        if t and t not in topics:
            topics.append(t)

    chash = _fingerprint(payload.url, payload.title)
    existing = db.execute(
        select(RawMaterial).where(RawMaterial.content_hash == chash)
    ).scalar_one_or_none()

    if existing:
        # 更新互动数与内容（用户可能补采了更完整的正文）
        existing.title = payload.title[:500]
        if payload.content.strip():
            existing.raw_content = payload.content[:20000]
            existing.summary = payload.content[:300]
        if metrics:
            existing.metrics = {**(existing.metrics or {}), **metrics}
        if topics:
            existing.topics_json = topics  # 复用 see below
        existing.source_url = payload.url[:1000]
        db.commit()
        db.refresh(existing)
        return {
            "ok": True,
            "action": "updated",
            "id": existing.id,
            "title": existing.title,
            "metrics": existing.metrics,
        }

    item = RawMaterial(
        source_type=SourceType.BROWSER,
        source_url=payload.url[:1000],
        source_name=f"{platform}·浏览器采集",
        title=payload.title[:500],
        raw_content=payload.content[:20000],
        summary=payload.content[:300],
        # 公开显示的昵称只作来源标注，不是个人数据挖掘
        author=(payload.author_display or "")[:200],
        own_flag=0,  # 别人家的内容，原创风险默认不低
        metrics=metrics,
        content_hash=chash,
        # 浏览器采来的是他人内容，二次创作原创风险高
        originality_risk="high" if not payload.keyword.strip() else "medium",
    )
    # 话题存进 extracted_elements（raw_materials 没有独立 topic 列）
    item.extracted_elements = {
        "topics": topics,
        "note_type": payload.note_type,
        "platform": platform,
        "user_keyword": payload.keyword.strip(),
    }
    db.add(item)
    db.commit()
    db.refresh(item)

    return {
        "ok": True,
        "action": "created",
        "id": item.id,
        "title": item.title,
        "metrics": metrics,
        "topics": topics,
        "note": f"已存入素材库（来源：{platform}·浏览器采集）",
    }


@router.get("/status", summary="扩展连接状态")
def status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """给扩展显示连接状态（后端在不在）。"""
    n = db.execute(
        select(RawMaterial).where(RawMaterial.source_type == SourceType.BROWSER)
    ).scalars().all()
    return {
        "ok": True,
        "collected": len(n),
        "allowed_platforms": sorted(set(ALLOWED_HOSTS.values())),
        "note": "采集只在用户点击时发生；不采用户昵称头像等可识别信息",
    }


@router.get("/recent", summary="最近采集的素材")
def recent(limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    """给扩展的浮层显示最近采了几条。"""
    rows = list(
        db.execute(
            select(RawMaterial)
            .where(RawMaterial.source_type == SourceType.BROWSER)
            .order_by(RawMaterial.fetched_at.desc())
            .limit(max(1, min(limit, 100)))
        ).scalars()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "source": r.source_name,
                "metrics": r.metrics,
                "url": r.source_url,
                "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            }
            for r in rows
        ],
    }
