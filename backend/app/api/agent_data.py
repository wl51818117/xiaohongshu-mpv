"""Agent 写入接口：供内核工具调用，把 Agent 产出真正落库。

这是「Agent 产出 → 程序可见」的关键链路。
内核工具的 execute 通过 HTTP 调这里，产出直接进数据库，
前端选题库/稿件库立即可见——而不是 Agent 在对话里空谈。

铁律：这些接口只允许内核调用，不暴露公网。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import RawMaterial, Topic, TopicStatus
from app.db.session import get_db

router = APIRouter(prefix="/api/agent/data", tags=["agent-data"])


# ── 请求模型 ────────────────────────────────────────────
class TopicCreate(BaseModel):
    """Agent 创建选题。"""

    title: str = Field(..., min_length=2, max_length=500)
    keyword_target: str = Field("", max_length=200)
    persona: str = Field("", max_length=200)
    value_type: str = Field("实用")
    differentiation: list[str] = Field(default_factory=list)
    material_id: int | None = None
    # Agent 的推理说明，用于溯源（存进 extracted 之外的字段）
    rationale: str = Field("", description="为什么这个选题好，AI 的判断依据")


class TopicUpdate(BaseModel):
    """Agent 更新选题状态。"""

    id: int
    status: str | None = None
    keyword_target: str | None = None


class MaterialSearch(BaseModel):
    """Agent 搜索素材。"""

    keyword: str = Field(..., min_length=1)
    limit: int = Field(10, ge=1, le=50)


# ── 写入 ────────────────────────────────────────────────
@router.post("/topics", summary="Agent 创建选题")
def create_topic(payload: TopicCreate, db: Session = Depends(get_db)) -> dict:
    """内核工具调用此接口，把 Agent 产出的选题写入库。"""
    # 去重：同标题已存在则不重复创建
    exists = db.query(Topic).filter(Topic.title == payload.title).first()
    if exists:
        # 字段必须与内核工具的 output.schema 一致：始终返回 ok/created/id/title
        return {
            "ok": True,
            "created": False,
            "id": exists.id,
            "title": exists.title,
            "reason": "同名选题已存在，未重复创建",
        }

    topic = Topic(
        title=payload.title,
        keyword_target=payload.keyword_target,
        persona=payload.persona,
        value_type=payload.value_type,
        differentiation=payload.differentiation,
        material_id=payload.material_id,
        status=TopicStatus.POOLED,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    # 若关联了素材，把理由写进素材的提炼字段，形成溯源链
    if payload.material_id and payload.rationale:
        material = db.get(RawMaterial, payload.material_id)
        if material:
            elements = dict(material.extracted_elements or {})
            elements.setdefault("agent_topics", []).append(
                {
                    "topic_id": topic.id,
                    "rationale": payload.rationale,
                }
            )
            material.extracted_elements = elements
            db.commit()

    return {"ok": True, "created": True, "id": topic.id, "title": topic.title}


@router.patch("/topics", summary="Agent 更新选题")
def update_topic(payload: TopicUpdate, db: Session = Depends(get_db)) -> dict:
    """内核工具调用此接口，更新选题状态或关键词。"""
    topic = db.get(Topic, payload.id)
    if not topic:
        raise HTTPException(status_code=404, detail="选题不存在")

    if payload.status:
        try:
            topic.status = TopicStatus(payload.status)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"非法状态：{payload.status}"
            )
    if payload.keyword_target is not None:
        topic.keyword_target = payload.keyword_target

    db.commit()
    db.refresh(topic)
    # 字段与内核 output.schema 对齐：始终返回 ok/id/status
    return {
        "ok": True,
        "id": topic.id,
        "status": topic.status.value if hasattr(topic.status, "value") else str(topic.status),
    }


# ── 读取（供 Agent 查询）────────────────────────────────
@router.get("/topics", summary="Agent 查询选题")
def list_topics(
    limit: int = 30,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """内核工具调用此接口，读取现有选题避免重复劳动。"""
    query = db.query(Topic).order_by(Topic.created_at.desc())
    if status:
        query = query.filter(Topic.status == status)
    rows = query.limit(limit).all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": t.id,
                "title": t.title,
                "keyword": t.keyword_target,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            }
            for t in rows
        ],
    }


@router.post("/materials/search", summary="Agent 搜索素材")
def search_materials(payload: MaterialSearch, db: Session = Depends(get_db)) -> dict:
    """内核工具调用此接口，在素材库里搜相关内容。"""
    kw = payload.keyword.strip()
    rows = (
        db.query(RawMaterial)
        .filter(RawMaterial.title.contains(kw) | RawMaterial.summary.contains(kw))
        .order_by(RawMaterial.fetched_at.desc())
        .limit(payload.limit)
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": m.id,
                "title": m.title,
                "source": m.source_name,
                "summary": (m.summary or "")[:200],
                "own": bool(m.own_flag),
            }
            for m in rows
        ],
    }
