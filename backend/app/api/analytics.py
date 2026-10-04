"""数据看板 API（M7）。

记录数据快照 → 提炼爆文要素 → 回流选题（闭环收口）。

★ 数据来源：手动录入或后续接官方 API。
  本服务**不代抓小红书数据** —— 合规考虑。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import analytics

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class MetricsIn(BaseModel):
    """录入一条笔记的表现数据。"""

    draft_id: int
    impressions: int = 0
    reads: int = 0
    likes: int = 0
    collects: int = 0
    comments: int = 0
    shares: int = 0
    follows: int = 0
    avg_read_seconds: float = 0.0
    completion_rate: float = Field(0.0, description="完播率 0-1")
    ctr: float = Field(0.0, description="点击率 0-1")
    read_rate: float = Field(0.0, description="完读率 0-1")


class CommentIn(BaseModel):
    """从评论里挖选题。"""

    comments: list[str] = Field(..., min_length=1)


@router.get("/dashboard", summary="看板总览")
def dashboard(db: Session = Depends(get_db)) -> dict:
    """各环节数量与进度。"""
    return analytics.dashboard(db)


@router.post("/metrics", summary="录入数据快照")
def record_metrics(payload: MetricsIn, db: Session = Depends(get_db)) -> dict:
    """记录发布后第 1/3/7/14 天的数据快照，自动算派生指标与投流建议。"""
    m = analytics.MetricsInput(
        impressions=payload.impressions,
        reads=payload.reads,
        likes=payload.likes,
        collects=payload.collects,
        comments=payload.comments,
        shares=payload.shares,
        follows=payload.follows,
        avg_read_seconds=payload.avg_read_seconds,
        completion_rate=payload.completion_rate,
        ctr=payload.ctr,
        read_rate=payload.read_rate,
    )
    try:
        derived = analytics.record_snapshot(db, payload.draft_id, m)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return {
        "ok": True,
        "draft_id": payload.draft_id,
        "derived": derived,
        "advice": derived["advice"],
    }


@router.get("/metrics/{draft_id}", summary="查某稿件的数据历史")
def get_metrics(draft_id: int, db: Session = Depends(get_db)) -> dict:
    """返回该稿件的快照序列与派生指标。"""
    from app.db.models import Draft

    d = db.get(Draft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="稿件不存在")
    v = d.validation or {}
    return {
        "draft_id": draft_id,
        "history": v.get("metrics_history", []),
        "latest": v.get("latest_metrics"),
        "derived": v.get("derived"),
    }


@router.get("/patterns", summary="提炼爆文要素")
def patterns(limit: int = 10, db: Session = Depends(get_db)) -> dict:
    """从有数据的稿件里提炼可复用规律。

    ★ 返回的是**规律**（结构/类型/人群/长度），不是可照抄的原文。
    """
    return analytics.extract_patterns(db, limit)


@router.post("/patterns/feed", summary="爆文要素回流选题库")
def feed_topics(limit: int = 10, db: Session = Depends(get_db)) -> dict:
    """把爆文要素转成新选题，闭合「发布→复盘→再选题」循环。

    新选题标注「二次创作」，强制走差异化，禁止原文照搬。
    """
    pats = analytics.extract_patterns(db, limit)
    result = analytics.feed_topics_from_patterns(db, pats)
    return {
        "ok": True,
        "sampled": pats.get("sampled", 0),
        **result,
        "note": (
            "新选题仅为**方向**，需 AI 按换人群/换场景/换角度重写；"
            "相似度超 60% 会被平台判抄袭"
        ),
    }


@router.post("/comment-topics", summary="从评论挖选题")
def from_comments(payload: CommentIn) -> dict:
    """评论里的高频提问 = 未被满足的搜索需求，最省力的选题来源。"""
    return {"items": analytics.comment_topics(payload.comments)}
