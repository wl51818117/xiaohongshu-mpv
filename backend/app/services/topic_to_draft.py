"""从选题转稿件（M3 核心流程）。

★ 流程修正（2026-10-04）：
  之前稿件是**孤立页面**，允许创建无主稿件（topic_id 为空），
  这在逻辑上是错的 —— 稿件必须从选题派生。

  正确流程：
    选题库 选题（已合规过筛）
       ↓ 转稿件（本模块）
    稿件（携带选题上下文：长尾词/人群/价值类型/差异化维度）
       ↓ 写正文
    校验通过 → 选题标记 done
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Draft, Topic, TopicStatus
from app.services import draft_validator


class TopicNotReadyError(Exception):
    """选题状态不允许转稿件。"""


def topic_to_draft(
    db: Session,
    topic_id: int,
    pipeline_type: str = "image",
) -> Draft:
    """把选题转成稿件。

    幂等：同一选题已有稿件时直接返回，不重复创建。
    """
    topic = db.get(Topic, topic_id)
    if not topic:
        raise ValueError("选题不存在")

    # 已建过稿件 → 返回现有稿件（幂等，避免重复建稿）
    existing = db.query(Draft).filter(Draft.topic_id == topic_id).first()
    if existing:
        return existing

    # 选题必须存在；archived 状态不允许复活
    if topic.status == TopicStatus.ARCHIVED:
        raise TopicNotReadyError("选题已归档，无法转稿件")

    keyword = topic.keyword_target or ""

    draft = Draft(
        topic_id=topic.id,
        pipeline_type=pipeline_type,
        # 标题预填长尾词（前端可改），正文留空等写
        title=keyword[: draft_validator.TITLE_MAX],
        body="",
        tags=[],
        ai_declaration=draft_validator.ai_declaration_text(pipeline_type),
        validation=draft_validator.validate_draft(
            title=keyword[: draft_validator.TITLE_MAX],
            body="",
            tags=[],
            keyword=keyword,
            ai_declaration=draft_validator.ai_declaration_text(pipeline_type),
            pipeline_type=pipeline_type,
        ),
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def topic_context(db: Session, topic_id: int) -> dict:
    """取选题的写作上下文 —— 写稿时该知道什么。"""
    topic = db.get(Topic, topic_id)
    if not topic:
        return {}

    # 同题稿件数（用于判断是新建还是续写）
    draft_count = db.query(Draft).filter(Draft.topic_id == topic_id).count()

    return {
        "topic_id": topic.id,
        "title": topic.title,
        "keyword_target": topic.keyword_target,
        "persona": topic.persona,
        "value_type": topic.value_type,
        "differentiation": topic.differentiation or [],
        "status": (
            topic.status.value
            if hasattr(topic.status, "value")
            else str(topic.status)
        ),
        "has_draft": draft_count > 0,
        "draft_count": draft_count,
        # 写作提示：把选题要素翻译成可执行的写作要求
        "writing_brief": _build_brief(topic),
    }


def _build_brief(topic: Topic) -> str:
    """把选题要素拼成写作简报（喂给 AI 生成用）。"""
    parts: list[str] = []
    if topic.keyword_target:
        parts.append(f"目标长尾词：{topic.keyword_target}（须出现在标题前 8-13 字与正文前 80 字）")
    if topic.persona:
        parts.append(f"目标人群：{topic.persona}")
    if topic.value_type:
        parts.append(f"价值类型：{topic.value_type}")
    if topic.differentiation:
        parts.append(f"差异化要求：{'、'.join(topic.differentiation)}（至少满足 3 项）")
    parts.append("正文 300-800 字，三段式：痛点开场 → 分点干货 → 结尾互动")
    parts.append("标签 3-5 个，覆盖品类词/场景词/人群词")
    parts.append("禁止：站外导流、极限词、编造个人经历")
    return "\n".join(parts)
