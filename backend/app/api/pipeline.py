"""主流程 API：RSS 采集 -> 选题转换 -> 选题库。

MVP 只实现这一条最小可运行主流程（剥离素材生成、发布等非必需功能）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import RawMaterial, SourceType, Topic, TopicStatus
from app.db.session import get_db
from app.services import rss_collector, topic_converter

router = APIRouter(prefix="/api", tags=["pipeline"])


# ── 请求/响应模型 ───────────────────────────────────────────
class FeedIn(BaseModel):
    """自定义 RSS 源。"""

    name: str = Field(..., examples=["机器之心"])
    url: str = Field(..., examples=["https://www.jiqizhixin.com/rss"])
    category: str = "综合"

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        """★ 安全（2026-10）：RSS URL 由服务端抓取，原来无任何校验，
        可打内网/元数据服务（SSRF）。"""
        from app.core.url_guard import UnsafeUrl, assert_safe_url

        try:
            return assert_safe_url(v)
        except UnsafeUrl as exc:
            raise ValueError(f"RSS 地址不安全：{exc}") from exc


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
async def convert_topics(
    limit: int = Query(20, ge=1, le=100),
    extract_keyword: bool = Query(True, description="是否用 AI 抽取长尾词"),
    db: Session = Depends(get_db),
) -> dict:
    """把素材经合规过筛后转成可执行选题。

    ★ 关键词改由 AI 抽取（2026-10）：正则切不出词，会产出
      「月广州海关监」这类滑窗碎片，进而污染稿件标题、写作简报、
      校验器整条链路。AI 不可用时宁可留空待人工填。
    """
    keywords: dict[int, str] = {}
    ai_failed = 0

    if extract_keyword:
        # 先取待转换素材（与 service 内部同条件）
        materials = (
            db.query(RawMaterial)
            .filter(~RawMaterial.topics.any())
            .order_by(RawMaterial.fetched_at.desc())
            .limit(limit)
            .all()
        )
        for m in materials:
            try:
                keywords[m.id] = await topic_converter.ai_keyword(m.title, m.summary)
            except Exception:  # noqa: BLE001
                ai_failed += 1

    result = topic_converter.convert_pending(db, limit=limit, keywords=keywords)
    if ai_failed:
        result["ai_failed"] = ai_failed
        result["note"] = (
            f"{ai_failed} 条素材的长尾词抽取失败，关键词留空需人工填写"
        )
    return {"ok": True, **result}


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
    # ★ 不再调 extract_keyword：那个函数已改为「抽不出就返回空串」
    #   （正则切不出词，与其给垃圾不如留空）。于是所有素材的 keyword
    #   都是空串，撞上下面的去重逻辑 → 全部被判「重复」。
    keyword = (payload.keyword or "").strip()

    # ── 去重：优先按素材 ID 判（最准：同一素材不该转两次）──
    existing = (
        db.query(Topic)
        .filter(Topic.material_id == mat.id)
        .first()
    )
    if existing:
        return {
            "ok": True, "created": False,
            "id": existing.id, "title": existing.title,
            "reason": f"该素材已转成选题 #{existing.id}，不重复创建",
        }

    # 其次按「标题 + 关键词」判，且**两者都不能为空**——
    # 空关键词会让所有素材互撞（实测踩过：提示「已在选题库」但其实是误判）
    if title and keyword:
        dup = (
            db.query(Topic)
            .filter(Topic.title == title, Topic.keyword_target == keyword)
            .first()
        )
        if dup:
            return {
                "ok": True, "created": False,
                "id": dup.id, "title": dup.title,
                "reason": f"库中已有相同标题与关键词的选题 #{dup.id}",
            }

    t = Topic(
        title=title[:500],
        keyword_target=keyword[:200],
        persona=(payload.persona or topic_converter.detect_persona(text) or "")[:200],
        value_type=payload.value_type,
        differentiation=topic_converter.build_differentiation(mat),
        material_id=mat.id,
        status=TopicStatus.POOLED,
        # 浏览器采来的是他人内容 → 二次创作原创风险高，标在选题上提醒
        originality_risk=(
            "high" if getattr(mat, "source_type", "") == SourceType.BROWSER else "low"
        ),
    )
    db.add(t)
    db.commit()
    db.refresh(t)

    result = {
        "ok": True,
        "created": True,
        "id": t.id,
        "title": t.title,
        "keyword": t.keyword_target,
    }
    if not keyword:
        # 明确告诉调用方：选题建了，但缺关键词，必须补
        result["warning"] = (
            "选题已创建，但**没有长尾词**。空关键词的稿件无法通过校验，"
            "请到选题库补上核心词（如「一次性内裤 差旅」）。"
        )
    return result


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
