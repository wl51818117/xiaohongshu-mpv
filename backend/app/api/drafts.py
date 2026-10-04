"""稿件 API：文案编辑、规格校验、Agent 写入。

这是内容生产的第三步（流程图「文案创作」环节）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Draft, Topic, TopicStatus
from app.db.session import get_db
from app.services import draft_validator

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


# ── 请求模型 ────────────────────────────────────────────
class DraftCreate(BaseModel):
    """创建稿件。"""

    topic_id: int | None = None
    pipeline_type: str = Field("image", description="image 图文 / video 视频")
    title: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    ai_declaration: str = ""


class DraftUpdate(BaseModel):
    """更新稿件内容。"""

    id: int
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    cover_url: str | None = None
    images: list[str] | None = None
    ai_declaration: str | None = None
    pipeline_type: str | None = None


class ValidateRequest(BaseModel):
    """仅校验，不保存（供前端实时提示用）。"""

    title: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    keyword: str = ""
    ai_declaration: str = ""
    pipeline_type: str = "image"


# ── 增删改查 ────────────────────────────────────────────
@router.post("", summary="创建稿件")
def create_draft(payload: DraftCreate, db: Session = Depends(get_db)) -> dict:
    """创建稿件并自动跑一次校验。"""
    keyword = ""
    topic = None
    if payload.topic_id:
        topic = db.get(Topic, payload.topic_id)
        if topic:
            keyword = topic.keyword_target or ""
            # 占用选题，推进状态
            if topic.status == TopicStatus.POOLED:
                topic.status = TopicStatus.CLAIMED

    declaration = payload.ai_declaration or draft_validator.ai_declaration_text(
        payload.pipeline_type
    )

    validation = draft_validator.validate_draft(
        title=payload.title,
        body=payload.body,
        tags=payload.tags,
        keyword=keyword,
        ai_declaration=declaration,
        pipeline_type=payload.pipeline_type,
    )

    # ★ 选题状态联动：校验通过即视为「已成稿」。
    #   之前只推进到 claimed 就停了，导致选题永远停在「建稿中」，
    #   可执行选题数被虚假占用、流程图进度也不准。
    if topic is not None and validation["passed"]:
        topic.status = TopicStatus.DONE

    draft = Draft(
        topic_id=payload.topic_id,
        pipeline_type=payload.pipeline_type,
        title=payload.title,
        body=payload.body,
        tags=payload.tags,
        ai_declaration=declaration,
        validation=validation,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    return {"ok": True, "id": draft.id, "validation": validation}


@router.get("", summary="稿件列表")
def list_drafts(
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    """列出稿件，附选题信息。"""
    rows = db.query(Draft).order_by(Draft.updated_at.desc()).limit(limit).all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": d.id,
                "topic_id": d.topic_id,
                "pipeline_type": d.pipeline_type,
                "title": d.title,
                "tags": d.tags,
                "ai_declaration": d.ai_declaration,
                "validation": d.validation,
                "topic_title": (d.topic.title if d.topic else ""),
                "keyword": (d.topic.keyword_target if d.topic else ""),
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in rows
        ],
    }


@router.get("/{draft_id}", summary="稿件详情")
def get_draft(draft_id: int, db: Session = Depends(get_db)) -> dict:
    """获取单篇稿件全文。"""
    d = db.get(Draft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="稿件不存在")
    return {
        "id": d.id,
        "topic_id": d.topic_id,
        "pipeline_type": d.pipeline_type,
        "title": d.title,
        "body": d.body,
        "tags": d.tags,
        "cover_url": d.cover_url,
        "images": d.images,
        "ai_declaration": d.ai_declaration,
        "validation": d.validation,
        "topic_title": (d.topic.title if d.topic else ""),
        "keyword": (d.topic.keyword_target if d.topic else ""),
    }


@router.patch("", summary="更新稿件")
def update_draft(payload: DraftUpdate, db: Session = Depends(get_db)) -> dict:
    """更新稿件内容并重跑校验。"""
    d = db.get(Draft, payload.id)
    if not d:
        raise HTTPException(status_code=404, detail="稿件不存在")

    if payload.title is not None:
        d.title = payload.title
    if payload.body is not None:
        d.body = payload.body
    if payload.tags is not None:
        d.tags = payload.tags
    if payload.cover_url is not None:
        d.cover_url = payload.cover_url
    if payload.images is not None:
        d.images = payload.images
    if payload.ai_declaration is not None:
        d.ai_declaration = payload.ai_declaration
    if payload.pipeline_type is not None:
        d.pipeline_type = payload.pipeline_type

    keyword = d.topic.keyword_target if d.topic else ""
    d.validation = draft_validator.validate_draft(
        title=d.title,
        body=d.body,
        tags=d.tags,
        keyword=keyword,
        ai_declaration=d.ai_declaration,
        pipeline_type=d.pipeline_type,
    )

    # ★ 选题状态联动：校验通过 → done；改回不通过 → 退回 claimed。
    #   否则「越改越糟」的稿件仍显示已成型，会误导选题决策。
    if d.topic is not None:
        if d.validation["passed"]:
            d.topic.status = TopicStatus.DONE
        elif d.topic.status == TopicStatus.DONE:
            d.topic.status = TopicStatus.CLAIMED

    db.commit()
    db.refresh(d)
    return {"ok": True, "id": d.id, "validation": d.validation}


@router.delete("/{draft_id}", summary="删除稿件")
def delete_draft(draft_id: int, db: Session = Depends(get_db)) -> dict:
    """删除稿件，并把选题退回可执行状态。"""
    d = db.get(Draft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="稿件不存在")

    if d.topic and d.topic.status == TopicStatus.CLAIMED:
        d.topic.status = TopicStatus.POOLED

    db.delete(d)
    db.commit()
    return {"ok": True, "deleted": draft_id}


# ── 校验 ────────────────────────────────────────────────
@router.post("/validate", summary="实时校验（不保存）")
def validate_only(payload: ValidateRequest) -> dict:
    """前端编辑时实时调用，即时反馈规格与合规问题。"""
    return draft_validator.validate_draft(
        title=payload.title,
        body=payload.body,
        tags=payload.tags,
        keyword=payload.keyword,
        ai_declaration=payload.ai_declaration,
        pipeline_type=payload.pipeline_type,
    )


@router.get("/stats/summary", summary="稿件统计")
def drafts_summary(db: Session = Depends(get_db)) -> dict:
    """给流程图提供真实的稿件数量。"""
    from sqlalchemy import func

    total = db.query(func.count(Draft.id)).scalar() or 0
    passed = 0
    for d in db.query(Draft).all():
        v = d.validation or {}
        if v.get("passed"):
            passed += 1
    return {"total": total, "passed": passed}
