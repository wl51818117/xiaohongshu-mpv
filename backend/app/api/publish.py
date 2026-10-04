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
    """
    result = pp.build_publish_package(db, draft_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
