"""发布队列 API（M5/M6）。

组装发布包 + 发布前自检。
★ 只做组装与自检，**不代提交平台** —— 发布必须人工点击。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import publish_package as pp

router = APIRouter(prefix="/api/publish", tags=["publish"])


@router.get("/package/{draft_id}", summary="组装发布包 + 发布前自检")
def get_package(draft_id: int, db: Session = Depends(get_db)) -> dict:
    """返回发布包与 12 项检查结果。

    任何一项阻塞性检查不通过时 ok=false，但仍返回完整报告 ——
    让人清楚还差什么，而不是只给一个失败。

    ★ 2026-10 修复：校验器原本只是「展示性旁观者」——
      4篇稿件 passed=False 但照样能进发布队列。
      现在把稿件校验作为**第一道闸门**显式列出，
      且不通过时明确告知「先修稿件，别发」。
    """
    from app.db.models import Draft

    draft = db.get(Draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="稿件不存在")

    result = pp.build_publish_package(db, draft_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    # 稿件校验作为独立闸门列出（不重复 12 项里的规格检查）
    v = draft.validation or {}
    gate = {
        "name": "稿件规格校验",
        "passed": bool(v.get("passed")),
        "issues": (v.get("spec_issues") or []) + (v.get("compliance_issues") or []),
        "cta_hints": v.get("cta_hints") or [],
    }
    result["draft_gate"] = gate
    if not gate["passed"]:
        result["blocked_reason"] = (
            "稿件未通过规格校验，不能发布。请先在稿件页修复："
            + "；".join(gate["issues"][:3])
        )
    return result
