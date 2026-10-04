"""主流程 API：RSS 采集 -> 选题转换 -> 选题库。

MVP 只实现这一条最小可运行主流程（剥离素材生成、发布等非必需功能）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import RawMaterial, Topic, TopicStatus
from app.db.session import get_db
from app.services import rss_collector, topic_converter

router = APIRouter(prefix="/api", tags=["pipeline"])


# ── 请求/响应模型 ───────────────────────────────────────────
class FeedIn(BaseModel):
    """自定义 RSS 源。"""

    name: str = Field(..., examples=["机器之心"])
    url: str = Field(..., examples=["https://www.jiqizhixin.com/rss"])
    category: str = "综合"


class CollectRequest(BaseModel):
    feeds: list[FeedIn] | None = None
    limit_per_feed: int = Field(10, ge=1, le=50)
    preset: str | None = Field(
        None, description="赛道预设 key，见 GET /api/pipeline/feeds"
    )


class TopicOut(BaseModel):
    """选题输出。"""

    id: int
    title: str
    keyword_target: str
    persona: str
    value_type: str
    differentiation: list[str]
    status: str
    source_name: str = ""


# ── 主流程三个环节 ─────────────────────────────────────────
@router.get("/pipeline/status", summary="查看各环节状态")
def pipeline_status(db: Session = Depends(get_db)) -> dict:
    """一眼看清流水线状态。"""
    total_materials = db.query(func.count(RawMaterial.id)).scalar() or 0
    total_topics = db.query(func.count(Topic.id)).scalar() or 0
    unconverted = (
        db.query(func.count(RawMaterial.id))
        .filter(~RawMaterial.topics.any())
        .scalar()
        or 0
    )
    pooled = (
        db.query(func.count(Topic.id))
        .filter(Topic.status == TopicStatus.POOLED)
        .scalar()
        or 0
    )
    return {
        "materials": total_materials,
        "topics": total_topics,
        "unconverted_materials": unconverted,
        "available_topics": pooled,
    }


@router.get("/pipeline/feeds", summary="赛道预设列表（兼容旧路径）")
def list_feeds() -> dict:
    """返回可用的赛道预设，供前端下拉选择。

    已合并到 /api/feeds（设置里可视化编辑），此路径保留兼容。
    """
    from app.api.feeds import list_feeds as _new_list_feeds

    return _new_list_feeds()


@router.post("/pipeline/collect", summary="环节一：RSS 采集入库")
def collect_rss(req: CollectRequest, db: Session = Depends(get_db)) -> dict:
    """从 RSS 源抓取资讯，去重后入库。

    不传 feeds 时用 preset 指定的赛道源；都不传则用第一个预设。
    """
    feeds = None
    if req.feeds:
        feeds = [
            rss_collector.FeedConfig(
                name=f.name, url=f.url, category=f.category
            )
            for f in req.feeds
        ]
    result = rss_collector.collect_rss(
        db,
        feeds=feeds,
        limit_per_feed=req.limit_per_feed,
        preset=req.preset,
    )
    return {"ok": True, **result}


@router.post("/pipeline/convert", summary="环节二：素材转选题")
def convert_topics(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """把素材经合规过筛后转成可执行选题。"""
    return {"ok": True, **topic_converter.convert_pending(db, limit=limit)}


@router.get("/topics", summary="选题库列表")
def list_topics(
    status: str | None = Query(None, description="按状态过滤"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """查看选题库。"""
    query = db.query(Topic).join(
        RawMaterial, Topic.material_id == RawMaterial.id, isouter=True
    ).order_by(Topic.created_at.desc())
    if status:
        query = query.filter(Topic.status == status)
    rows = query.limit(limit).all()

    items = [
        TopicOut(
            id=t.id,
            title=t.title,
            keyword_target=t.keyword_target,
            persona=t.persona,
            value_type=t.value_type,
            differentiation=t.differentiation,
            status=t.status.value if hasattr(t.status, "value") else str(t.status),
            source_name=(t.material.source_name if t.material else ""),
        )
        for t in rows
    ]
    return {"count": len(items), "items": [i.model_dump() for i in items]}


@router.get("/materials", summary="素材库列表")
def list_materials(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """查看已采集素材。"""
    rows = (
        db.query(RawMaterial)
        .order_by(RawMaterial.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": m.id,
                "source_name": m.source_name,
                "source_type": (
                    m.source_type.value
                    if hasattr(m.source_type, "value")
                    else str(m.source_type)
                ),
                "title": m.title,
                "own_flag": bool(m.own_flag),
                "has_topic": bool(m.topics),
            }
            for m in rows
        ],
    }


@router.get("/health", summary="健康检查")
def health() -> dict:
    return {"ok": True}


# ══════════════════════════════════════════════════════════════
# 选题管理：删除 + 从素材直接转选题
# ══════════════════════════════════════════════════════════════

class MaterialToTopicIn(BaseModel):
    """从素材直接建选题（用户在素材库点「加入选题」时用）。"""

    material_id: int
    title: str = Field("", description="自定义选题标题，留空则用素材原标题")
    keyword: str = Field("", description="目标长尾词，留空则自动抽取")
    persona: str = ""
    value_type: str = "实用"


@router.post("/materials/{material_id}/to-topic", summary="素材直接转选题")
def material_to_topic(
    material_id: int,
    payload: MaterialToTopicIn,
    db: Session = Depends(get_db),
) -> dict:
    """把一条素材转成选题。

    素材库里的内容默认只是「待转换」，用户也可以主动挑一条直接建选题。
    """
    from app.db.models import RawMaterial
    from app.services import topic_converter

    mat = db.get(RawMaterial, material_id)
    if not mat:
        raise HTTPException(status_code=404, detail="素材不存在")

    text = f"{mat.title} {mat.summary or ''}"

    # 合规预检：违规素材不允许转选题
    passed, hits = topic_converter.check_compliance(text)
    if not passed:
        raise HTTPException(
            status_code=400,
            detail=f"素材命中合规黑名单（{'、'.join(hits[:3])}），不能转选题",
        )

    title = (payload.title or "").strip() or mat.title
    keyword = (payload.keyword or "").strip() or topic_converter.extract_keyword(
        mat.title, mat.summary or ""
    )

    # 同一素材已有选题则不重复创建
    existing = (
        db.query(Topic)
        .filter(Topic.title == title, Topic.keyword_target == keyword)
        .first()
    )
    if existing:
        return {"ok": True, "created": False, "id": existing.id, "title": existing.title}

    t = Topic(
        title=title[:500],
        keyword_target=keyword[:200],
        persona=(payload.persona or topic_converter.detect_persona(text) or "")[:200],
        value_type=payload.value_type,
        differentiation=topic_converter.build_differentiation(mat),
        material_id=mat.id,
        status=topic_converter.TopicStatus.POOLED,
    )
    db.add(t)
    db.commit()
    db.refresh(t)

    return {
        "ok": True,
        "created": True,
        "id": t.id,
        "title": t.title,
        "keyword": t.keyword_target,
    }


@router.delete("/topics/{topic_id}", summary="删除选题")
def delete_topic(topic_id: int, db: Session = Depends(get_db)) -> dict:
    """删除选题。若该选题已有稿件，一并删除稿件（避免孤儿数据）。"""
    from app.db.models import Draft

    t = db.get(Topic, topic_id)
    if not t:
        raise HTTPException(status_code=404, detail="选题不存在")

    drafts = db.query(Draft).filter(Draft.topic_id == topic_id).all()
    for d in drafts:
        db.delete(d)
    db.delete(t)
    db.commit()

    return {"ok": True, "deleted_topic": topic_id, "deleted_drafts": len(drafts)}
