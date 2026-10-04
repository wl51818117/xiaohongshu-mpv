"""稿件 API：文案编辑、规格校验、Agent 写入。

这是内容生产的第三步（流程图「文案创作」环节）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Draft, Topic, TopicStatus
from app.db.session import get_db
from app.core.config import settings
from app.services import draft_validator
from app.services import topic_to_draft

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


# ── 请求模型 ────────────────────────────────────────────
class DraftCreate(BaseModel):
    """创建稿件。

    ★ topic_id **必填**：稿件必须从选题派生（原流程错误：
      曾允许无主稿件，导致稿件与选题脱节）。
    """

    topic_id: int = Field(..., description="所属选题 id，必须从选题库转稿件")
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
    topic = db.get(Topic, payload.topic_id) if payload.topic_id else None
    if payload.topic_id:
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


# ══════════════════════════════════════════════════════════════
# 从选题转稿件（正确的流程入口）
# ══════════════════════════════════════════════════════════════

def _clean_copy(text: str) -> str:
    """清洗模型输出：剥掉推理残留、markdown 标记、空话开场白。

    小模型（deepseek-flash）经常把思考过程一起吐出来，
    直接落库会导致「正文 2000+字」且首句是英文——必须清洗。
    """
    import re as _re

    s = text.strip()
    if not s:
        return ""

    # 1) 剥markdown 标记
    s = _re.sub(r"^#{1,6}\s*", "", s, flags=_re.M)      # 标题
    s = _re.sub(r"\*\*(.+?)\*\*", r"\1", s)              # 粗体
    s = _re.sub(r"__(.+?)__", r"\1", s)
    s = _re.sub(r"\*([^*\n]{1,40})\*", r"\1", s)         # 斜体

    # 2) 常见的「以下是…」包装
    s = _re.sub(
        r"^(好的|当然|以下是|下面是|这是)[^\n]{0,40}?(正文|笔记|文案)?[:：]\s*",
        "",
        s,
    )

    # 3) 剥英文推理段（模型有时先写一段英文分析）
    lines = s.split("\n")
    while lines and (
        not lines[0].strip()
        or _re.match(r"^\s*[A-Za-z][A-Za-z\s,.:;'\"()\-]{20,}", lines[0])
        or _re.search(r"\b(I need|Let me|Here's|First,|Note:|I'll)\b", lines[0], _re.I)
    ):
        lines.pop(0)
    s = "\n".join(lines).strip()

    # 4) 压缩多余空行
    s = _re.sub(r"\n{3,}", "\n\n", s)

    # 5) 超长则截到上限（模型不听话时的兜底）
    if len(s) > draft_validator.BODY_MAX * 2:
        s = s[: draft_validator.BODY_MAX * 2]

    return s.strip()


def _stored_api_key() -> str:
    """读内核鉴权凭据。

    优先级：**环境变量 > 设置中保存的 Key**
    —— 与内核自身的凭据优先级保持一致（内核也是环境变量优先）。

    存在设置里的是用户填的「DeepSeek API Key」；
    而内核鉴权用的可能是另一个令牌（appToken），两者不一定相同。
    所以先看环境变量，没有再回落设置。
    """
    import os

    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        f = Path(settings.data_dir) / "secrets.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            return str(data.get("api_key") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


class TopicToDraftIn(BaseModel):
    """从选题转稿件的请求。"""

    topic_id: int
    pipeline_type: str = Field("image", description="image 图文 / video 视频")


class GenerateCopyIn(BaseModel):
    """AI 生成正文请求。"""

    draft_id: int


@router.post("/from-topic", summary="从选题转稿件")
def create_from_topic(payload: TopicToDraftIn, db: Session = Depends(get_db)) -> dict:
    """把选题转成稿件（幂等：已有稿件则直接返回）。

    这是**唯一正确的建稿入口** —— 稿件必须带着选题上下文。
    """
    try:
        draft = topic_to_draft.topic_to_draft(
            db, payload.topic_id, payload.pipeline_type
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except topic_to_draft.TopicNotReadyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    ctx = topic_to_draft.topic_context(db, payload.topic_id)
    return {
        "ok": True,
        "draft_id": draft.id,
        "created": bool(ctx.get("has_draft")),
        "context": ctx,
        "draft": _draft_brief(draft),
    }


@router.get("/topic-context/{topic_id}", summary="取选题写作上下文")
def get_topic_context(topic_id: int, db: Session = Depends(get_db)) -> dict:
    """写稿前先拿选题要素：长尾词、人群、价值类型、差异化要求。"""
    ctx = topic_to_draft.topic_context(db, topic_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="选题不存在")
    return ctx


@router.post("/generate-copy", summary="AI 生成正文")
async def generate_copy(payload: GenerateCopyIn, db: Session = Depends(get_db)) -> dict:
    """按选题上下文生成正文初稿。

    ★ 走内核（Agent 编排），让模型带着写作简报生成。
      内核不可用时返回明确错误，不静默失败。
    """
    draft = db.get(Draft, payload.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="稿件不存在")
    if not draft.topic_id:
        raise HTTPException(
            status_code=400, detail="该稿件未关联选题，无法生成（请从选题库转稿件）"
        )

    ctx = topic_to_draft.topic_context(db, draft.topic_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="选题不存在")

    brief = ctx.get("writing_brief", "")
    prompt = (
        f"你现在扮演小红书爆款文案写手。只输出正文本身，"
        f"不要输出任何思考过程、说明、解释或 markdown 标记。\n\n"
        f"选题：{ctx.get('title')}\n"
        f"目标长尾词：{ctx.get('keyword_target', '')}\n"
        f"目标人群：{ctx.get('persona') or '不限'}\n\n"
        f"写作要求：\n{brief}\n\n"
        f"再次强调输出规则：\n"
        f"1. **只输出正文**，第一句话就是正文开头\n"
        f"2. **必须用中文**\n"
        f"3. 长度 {draft_validator.BODY_MIN}-{draft_validator.BODY_MAX} 个汉字\n"
        f"4. 关键词「{ctx.get('keyword_target', '')}」必须出现在**前 80 字内**\n"
        f"5. 不要写「好的」「以下是」「希望对你有帮助」这类开场白\n"
        f"6. 禁止出现微信/电话/二维码等导流信息与极限词\n"
    )

    # 经后端转发到内核（绝不浏览器直连内核）
    # ★ 内核自 hbridge v2.1 起要求鉴权：需带票据或静态凭据，
    #   否则一律 401。这里复用设置中保存的 API Key。
    headers = {"content-type": "application/json"}
    api_key = _stored_api_key()
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{settings.kernel_base_url}/v1/chat",
                json={"text": prompt, "sessionId": f"draft-{draft.id}"},
                headers=headers,
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"内核不可达（{settings.kernel_base_url}）：{exc}。"
            f"请先启动提取harness/start-all.cmd",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"内核返回 {resp.status_code}：{resp.text[:160]}"
        )

    # 解析 SSE，取正文文本
    text_parts: list[str] = []
    for line in resp.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            payload_obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = payload_obj.get("text")
        if isinstance(t, str):
            text_parts.append(t)

    body = _clean_copy("".join(text_parts))
    if not body:
        raise HTTPException(status_code=502, detail="内核未返回可用正文")

    # 落库 + 重跑校验
    draft.body = body
    draft.validation = draft_validator.validate_draft(
        title=draft.title,
        body=body,
        tags=draft.tags,
        keyword=ctx.get("keyword_target", ""),
        ai_declaration=draft.ai_declaration,
        pipeline_type=draft.pipeline_type,
    )
    if draft.validation["passed"] and draft.topic:
        draft.topic.status = TopicStatus.DONE
    db.commit()
    db.refresh(draft)

    return {
        "ok": True,
        "draft_id": draft.id,
        "body": body,
        "validation": draft.validation,
    }


def _draft_brief(draft: Draft) -> dict:
    """稿件精简信息。"""
    return {
        "id": draft.id,
        "topic_id": draft.topic_id,
        "title": draft.title,
        "body_len": len(draft.body or ""),
        "tags": draft.tags,
        "ai_declaration": draft.ai_declaration,
        "validation": draft.validation,
    }
