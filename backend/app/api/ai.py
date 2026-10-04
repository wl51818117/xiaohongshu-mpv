"""AI 创作 API：标题生成 / 多轮打磨 / 标签推荐。

配合 WritingDesk 使用，构成「选题→ 标题 → 正文 → 打磨 → 标签」完整创作链。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Draft, Topic
from app.db.session import get_db
from app.services import ai_writer

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ── 模型 ────────────────────────────────────────────────
class TitlesIn(BaseModel):
    """批量生成标题。"""

    topic_id: int | None = None
    topic: str = Field("", description="直接给标题文本时用")
    keyword: str = Field("", description="目标长尾词")
    persona: str = ""
    value_type: str = "实用"
    n: int = Field(10, ge=1, le=20, description="生成数量")
    draft_id: int | None = Field(None, description="给定时从稿件取上下文")


class PolishIn(BaseModel):
    """单次打磨。"""

    draft_id: int | None = None
    body: str = Field("", description="直接给正文时用")
    action: str = Field(
        "humanize",
        description="humanize 去AI味 / polish 润色 / shorten 精简 / expand 扩写 / hook 改开头",
    )
    keyword: str = ""
    extra: str = Field("", description="追加要求，用于多轮对话")


class ChatIn(BaseModel):
    """多轮对话打磨。"""

    draft_id: int | None = None
    body: str = ""
    message: str = Field(..., min_length=1)
    keyword: str = ""


class TagsIn(BaseModel):
    """标签推荐。"""

    draft_id: int | None = None
    title: str = ""
    body: str = ""
    persona: str = ""


# ── 标题 ────────────────────────────────────────────────
@router.post("/titles", summary="批量生成爆款标题候选")
async def gen_titles(payload: TitlesIn, db: Session = Depends(get_db)) -> dict:
    """一次性产出 n 个标题候选，由用户挑选后导入。"""
    topic = payload.topic
    keyword = payload.keyword
    persona = payload.persona

    if payload.draft_id:
        d = db.get(Draft, payload.draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="稿件不存在")
        topic = topic or d.title
        keyword = keyword or (d.topic.keyword_target if d.topic else "")
        persona = persona or (d.topic.persona if d.topic else "")

    if not topic.strip():
        raise HTTPException(status_code=400, detail="缺少选题内容")

    try:
        titles = await ai_writer.gen_titles(
            topic=topic,
            keyword=keyword,
            persona=persona,
            vtype=payload.value_type,
            n=payload.n,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "count": len(titles), "titles": titles}


# ── 打磨 ────────────────────────────────────────────────
@router.post("/polish", summary="单次打磨（去AI味/润色/精简/扩写/改开头）")
async def polish(payload: PolishIn, db: Session = Depends(get_db)) -> dict:
    """按指定动作打磨正文，返回新正文与规格校验。"""
    body = payload.body
    keyword = payload.keyword

    if payload.draft_id:
        d = db.get(Draft, payload.draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="稿件不存在")
        body = body or d.body
        keyword = keyword or (d.topic.keyword_target if d.topic else "")

    if not body.strip():
        raise HTTPException(status_code=400, detail="正文为空，无法打磨")

    try:
        text = await ai_writer.polish(
            action=payload.action,
            body=body,
            keyword=keyword,
            extra=payload.extra,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 存回稿件（若有）
    validation = None
    if payload.draft_id:
        d = db.get(Draft, payload.draft_id)
        if d:
            from app.services import draft_validator

            d.body = text
            d.validation = draft_validator.validate_draft(
                title=d.title,
                body=text,
                tags=d.tags,
                keyword=keyword,
                ai_declaration=d.ai_declaration,
                pipeline_type=d.pipeline_type,
            )
            db.commit()
            db.refresh(d)
            validation = d.validation

    return {"ok": True, "body": text, "validation": validation}


@router.post("/chat", summary="多轮对话打磨")
async def chat(payload: ChatIn, db: Session = Depends(get_db)) -> dict:
    """带当前正文做多轮对话，支持「再口语一点」这类追加指令。"""
    body = payload.body
    keyword = payload.keyword

    if payload.draft_id:
        d = db.get(Draft, payload.draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="稿件不存在")
        body = body or d.body
        keyword = keyword or (d.topic.keyword_target if d.topic else "")

    if not body.strip():
        raise HTTPException(status_code=400, detail="正文为空，无法打磨")

    try:
        text = await ai_writer.chat(
            body=body, message=payload.message, keyword=keyword
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    validation = None
    if payload.draft_id:
        d = db.get(Draft, payload.draft_id)
        if d:
            from app.services import draft_validator

            d.body = text
            d.validation = draft_validator.validate_draft(
                title=d.title,
                body=text,
                tags=d.tags,
                keyword=keyword,
                ai_declaration=d.ai_declaration,
                pipeline_type=d.pipeline_type,
            )
            db.commit()
            db.refresh(d)
            validation = d.validation

    return {"ok": True, "body": text, "validation": validation}


# ── 标签 ────────────────────────────────────────────────
@router.post("/tags", summary="智能标签推荐")
async def gen_tags(payload: TagsIn, db: Session = Depends(get_db)) -> dict:
    """按标题正文推荐标签，混合品类/场景/人群三类。"""
    title = payload.title
    body = payload.body
    persona = payload.persona

    if payload.draft_id:
        d = db.get(Draft, payload.draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="稿件不存在")
        title = title or d.title
        body = body or d.body
        persona = persona or (d.topic.persona if d.topic else "")

    if not title.strip():
        raise HTTPException(status_code=400, detail="缺少标题，无法推荐标签")

    try:
        tags = await ai_writer.gen_tags(title=title, body=body, persona=persona)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "count": len(tags), "tags": tags}


# ── 提示词预览（规整性检查用，不调模型）────────────────────

class PromptPeekIn(BaseModel):
    """预览写作提示词。"""

    topic: str = Field("秋冬新品内衣怎么选", description="选题文本")
    keyword: str = Field("内衣 面料")
    persona: str = Field("25-35岁女性")
    value_type: str = Field("实用")
    body: str = Field("（正文示例）看成分标签，面料成分占比最大的才是主料。", description="打磨预览用")
    n: int = Field(10, ge=1, le=20)


@router.post("/prompts/peek", summary="预览写作提示词")
def peek_prompts(req: PromptPeekIn) -> dict:
    """返回将要发给模型的提示词，供检查写法是否规整。

    ★ 不调模型、零消耗。让提示词成为「可检视的资产」而不是黑盒。
    """
    core = ai_writer._core_terms(req.keyword)
    core_str = "、".join(core) if core else req.keyword

    return {
        "ok": True,
        "titles": {
            "name": "标题生成",
            "formula": ai_writer.TITLE_FORMULAS,
            "prompt": ai_writer.TITLE_PROMPT.format(
                n=req.n, topic=req.topic, persona=req.persona or "不限",
                vtype=req.value_type, core=core_str,
                formulas=ai_writer.TITLE_FORMULAS,
            ),
        },
        "tags": {
            "name": "标签推荐",
            "prompt": ai_writer.TAG_PROMPT.format(
                title=req.topic, summary=req.body[:300], persona=req.persona or "不限",
            ),
        },
        "polish": [
            {
                "name": cfg["label"],
                "action": key,
                "prompt": cfg["prompt"].format(
                    body=req.body, keyword=req.keyword or "（无需埋词）",
                    min_=300, max_=800,
                ),
            }
            for key, cfg in ai_writer.POLISH_ACTIONS.items()
        ],
        "chat": {
            "name": "多轮打磨",
            "prompt": (
                f"你在帮一篇小红书笔记打磨正文。当前正文：\n\n"
                f"【当前正文】\n{req.body}\n\n"
                f"【用户要求】\n（在此输入指令）\n\n"
                f"要求：\n1. 按用户要求改，只输出改后的正文\n"
                f"2. 保持长尾词「{req.keyword or '（无）'}」自然出现\n"
                f"3. 长度 300-800 字\n4. 不要输出解释"
            ),
        },
    }
