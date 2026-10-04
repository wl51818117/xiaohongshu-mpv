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
    """把选题要素拼成写作简报（喂给 AI 生成用）。

    ★ 重构：原来只给了题材和一句「三段式」，太抽象 ——
      实测模型交出的是「正确但平庸」的稿子：结构松散、字数贴边、
      开头是「大家好」这类无效钩子。
      现在给**可执行的规格**：分段结构、每段写什么、字数分配、
      风格约束、明确的开头与结尾写法。
    """
    import re as _re

    parts: list[str] = []

    if topic.keyword_target:
        terms = [t for t in _re.split(r"[\s、,，/|+]+", topic.keyword_target) if t]
        terms.sort(key=len, reverse=True)
        core = "、".join(terms[:2]) if terms else topic.keyword_target
        parts.append(
            f"【目标长尾词】{topic.keyword_target}\n"
            f"  → 其中核心词「{core}」必须出现在：标题前 10 字内、正文前 80 字内，"
            f"全文自然出现 2-3 次（不要堆砌，超过 3 次可能限流）"
        )
    if topic.persona:
        parts.append(f"【目标人群】{topic.persona}（第二人称「你」对话，读者要有「说的就是我」的代入感）")
    if topic.value_type:
        parts.append(f"【价值类型】{topic.value_type}")
    if topic.differentiation:
        parts.append(f"【差异化要求】{'、'.join(topic.differentiation)}（至少满足 3 项，不要写成泛泛而谈）")

    parts.append(
        """
【正文结构（严格四段，字数已分配）】
1. **钩子开头（60-100字）**：第一句必须是痛点、反常识或具体场景。
   ✗ 禁止：「大家好」「今天分享」「最近很多姐妹问我」这类无效开场
   ✓ 示例：「排队 40 分钟才发现，这个地方根本不用去。」
2. **共鸣展开（60-100字）**：把痛点说透，让目标人群对号入座。
   要有具体的个人化细节（我踩过的坑、当时的犹豫、真实场景）。
3. **干货主体（150-350字）**：分 3 点，每点一行小标题 + 具体做法。
   每点必须给**可执行的动作**，不是「要注意质量」这种空话。
4. **收尾（40-80字）**：一句总结 + 一个自然的行动建议。
   ✗ 禁止：「关注我」「评论区扣 1」「点赞收藏」等导流话术（平台判违规）
   ✓ 示例：「把这三个判断记住，下次逛超市能省半小时。」

【字数】全文 380-650 字（硬性要求，低于 300 会被折叠，高于 800 完读率掉）

【风格要求】
- 像真人写的笔记，不是文章：多用短句（15字内），少用书面语
- 具体 > 抽象：写「排队 40 分钟」不写「耗费大量时间」
- 不要排比句、不要「不仅…而且…」「总而言之」这类连接词
- 不要用 emoji 堆砌，最多 0-2 个

【标签】3-5 个，混合三类：品类词（买什么）、场景词（什么场合）、人群词（谁用）

【行动号召（CTA）——★合规做法】
小红书 2026-09-18 新规后，**站内私信是唯一安全的主成交渠道**。
✓ 可以写：「有疑问评论区问我」「详细尺码看主页合集」「店铺链接在主页」
✗ 不能写：加微信/留电话/发二维码/淘宝口令/谐音变体

结尾用一句自然的行动建议，不要用「关注我」「评论区扣 1」「点赞收藏」
这类诱导互动话术（平台明确列为违规）。
想引导咨询就直接说「私信我说尺码」——这是合规且有效的。

【禁止事项】
- **站外导流**（微信/电话/二维码/淘宝口令/谐音变体）—— 平台处罚最重，
  2026 新规最高扣 2 万，且关联账号同罪
- 极限词（最/第一/绝对/全网最好/天花板）
- 医疗功效（治愈/消炎/杀菌/医用）
- 无检测报告支撑的资质宣称（抗菌/A类/母婴级/孕产妇可用）
- 编造具体数字或虚假经历""".strip()
    )
    return "\n".join(parts)
