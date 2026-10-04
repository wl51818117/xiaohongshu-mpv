"""知识库 API：检索、沉淀、Obsidian 导入、错误本回顾。

供前端「知识库」页签使用，同时被内核工具调用（workbench-ops）。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import knowledge as kb

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# ── 请求模型 ────────────────────────────────────────────────

class SearchIn(BaseModel):
    query: str = Field(..., min_length=1, description="检索词")
    limit: int = Field(5, ge=1, le=20)
    kind: str = Field("", description="pitfall / review / spec / insight")
    tag: str = Field("")


class AddIn(BaseModel):
    title: str = Field(..., min_length=1)
    kind: Literal["pitfall", "review", "spec", "insight"] = "pitfall"
    why: str = ""
    how: str = ""
    pitfall: str = ""
    related: str = ""
    tags: str = ""


class MistakeIn(BaseModel):
    scene: str = ""
    symptom: str = Field(..., min_length=1)
    cause: str = ""
    fix: str = ""


class ImportIn(BaseModel):
    root: str = Field(..., description="Obsidian 库根目录")
    subdir: str = Field("30-知识库")


# ── 检索 ────────────────────────────────────────────────────

@router.post("/search", summary="检索知识库")
def search(req: SearchIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """关键词检索。命中条目会累计 hit_count，供后续挑高频复看。"""
    items = kb.search_knowledge(
        db, query=req.query, limit=req.limit, kind=req.kind, tag=req.tag
    )
    kb.bump_hits(db, [i["id"] for i in items])
    return {"count": len(items), "items": items}


@router.get("/stats", summary="知识库概览")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return kb.knowledge_stats(db)


@router.get("/items", summary="列出全部条目")
def list_items(
    kind: str = "",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """不传 query 时用于浏览（按最近更新排序）。"""
    from sqlalchemy import select

    from app.db.models import KnowledgeItem

    stmt = select(KnowledgeItem)
    if kind:
        stmt = stmt.where(KnowledgeItem.kind == kind)
    stmt = stmt.order_by(KnowledgeItem.updated_at.desc()).limit(limit)
    rows = list(db.execute(stmt).scalars())
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id, "title": r.title, "kind": r.kind,
                "why": r.why, "how": r.how, "pitfall": r.pitfall,
                "tags": r.tags, "hit_count": r.hit_count,
                "source": r.source,
            }
            for r in rows
        ],
    }


# ── 沉淀 ────────────────────────────────────────────────────

@router.post("/items", summary="新增或更新知识条目")
def add(req: AddIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """同标题视为更新（避免重复沉淀）。"""
    return kb.add_knowledge(
        db=db, title=req.title, kind=req.kind, why=req.why,
        how=req.how, pitfall=req.pitfall, related=req.related, tags=req.tags,
    )


@router.delete("/items/{item_id}", summary="删除知识条目")
def delete(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.db.models import KnowledgeItem

    r = db.get(KnowledgeItem, item_id)
    if not r:
        return {"ok": False, "error": "条目不存在"}
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/import/obsidian", summary="从 Obsidian 导入")
def import_obs(req: ImportIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """按标题 upsert，重复导入不产生重复条目。"""
    return kb.import_obsidian(db, req.root, req.subdir)


# ── 错误本与回顾 ────────────────────────────────────────────

@router.get("/mistakes", summary="查错误本")
def mistakes(
    query: str = "",
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """query 为空 → 返回最久未复盘的（供定期回顾）。"""
    items = kb.search_mistakes(db, query, limit)
    return {"count": len(items), "items": items}


@router.post("/mistakes", summary="记一条错误")
def add_mistake(req: MistakeIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    return kb.add_mistake(db, scene=req.scene, symptom=req.symptom,
                          cause=req.cause, fix=req.fix)


@router.post("/mistakes/{mistake_id}/review", summary="标记已复盘")
def review(mistake_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return kb.mark_reviewed(db, mistake_id)


# ── 持续增强：投票分 / 晋升 / 审查 ──────────────────────────

class VoteIn(BaseModel):
    """对一条经验投票。"""

    title: str = Field(..., min_length=1)
    action: Literal["add", "agree", "edit", "remove", "challenge"] = Field(
        "add",
        description=(
            "add 新增(+2) / agree 再次验证(+1) / edit 改写(+1) / "
            "remove 质疑(-1，库满时-3) / challenge 质疑(-1)"
        ),
    )
    why: str = ""
    how: str = ""
    pitfall: str = ""
    tags: str = ""


@router.post("/vote", summary="对经验投票（维护投票分）")
def vote(req: VoteIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """投票分借鉴 ExpeL 的规则库算法。

    分数 <= 0 自动退休（不物理删除，保留历史）。
    """
    return kb.apply_vote(
        db, title=req.title, action=req.action, why=req.why,
        how=req.how, pitfall=req.pitfall, tags=req.tags,
    )


@router.post("/mistakes/{mistake_id}/promote", summary="复盘结论晋升为正式经验")
def promote(mistake_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """错误本 → 知识库。这是「复盘」的核心动作。

    借鉴 ADR 的做法：被否决/出错的记录永不删除，
    晋升后仍能在错误本里看到当时的场景。
    """
    return kb.promote_mistake(db, mistake_id)


@router.post("/audit", summary="定期审查：退休低分经验")
def audit() -> dict[str, Any]:
    """借鉴 Generative Agents 的 importance_trigger：
    库变大时自动批量审查，而不是无限增长。
    """
    from app.services import experience as exp

    return exp.audit_low_score()


@router.get("/growth", summary="持续增强概览")
def growth(db: Session = Depends(get_db)) -> dict[str, Any]:
    """看闭环是否真的在转：沉淀了多少、投票分布、活跃度。"""
    from sqlalchemy import func, select

    from app.db.models import KnowledgeItem

    total = db.execute(select(func.count(KnowledgeItem.id))).scalar_one()
    active = db.execute(
        select(func.count(KnowledgeItem.id)).where(KnowledgeItem.status == "active")
    ).scalar_one()
    retired = total - active
    strong = db.execute(
        select(func.count(KnowledgeItem.id)).where(KnowledgeItem.score >= 4)
    ).scalar_one()
    weak = db.execute(
        select(func.count(KnowledgeItem.id)).where(KnowledgeItem.score <= 2)
    ).scalar_one()
    hits = db.execute(select(func.sum(KnowledgeItem.hit_count))).scalar_one() or 0
    mistakes = kb.search_mistakes(db, "", 999)
    pending = sum(1 for m in mistakes if not m["reviewed"])

    return {
        "total": total,
        "active": active,
        "retired": retired,
        "strong": strong,
        "weak": weak,
        "total_hits": int(hits),
        "pending_review": pending,
        "top": [
            {"title": r.title, "score": r.score, "hits": r.hit_count}
            for r in db.execute(
                select(KnowledgeItem)
                .where(KnowledgeItem.status == "active")
                .order_by(KnowledgeItem.score.desc(), KnowledgeItem.hit_count.desc())
                .limit(8)
            ).scalars()
        ],
        "weakest": [
            {"title": r.title, "score": r.score, "hits": r.hit_count}
            for r in db.execute(
                select(KnowledgeItem)
                .where(KnowledgeItem.status == "active")
                .order_by(KnowledgeItem.score.asc(), KnowledgeItem.hit_count.asc())
                .limit(5)
            ).scalars()
        ],
    }
