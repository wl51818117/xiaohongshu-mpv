"""数据模型：素材库 / 选题 / 稿件 / 内核工具调用记录。

设计依据：
- docs/06-采集与发布方案修正v2.md（三源合一采集）
- docs/07-自有爆款回采与二次创作方案v3.md（二次创作四条规则）
- docs/08-终极选型方案v4单账号版.md（单账号精简）

MVP 只保留主流程必需表：raw_materials -> topics -> drafts
资产、发布、数据回流等表在P4+ 阶段按需扩展。
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


class SourceType(str, enum.Enum):
    """采集来源类型（三源合一）。"""

    RSS = "rss"          # RSS / 新闻源
    OWN_NOTE = "own_note"  # 自有账号爆款回采
    HOT_LIST = "hot_list"  # 全网热点聚合（后续阶段）


class TopicStatus(str, enum.Enum):
    """选题状态机。

    collected -> pooled -> claimed -> writing -> archived

    MVP 只用到 pooled（可执行选题）与 claimed（已占用建稿）。
    """

    POOLED = "pooled"      # 已入库，可执行
    CLAIMED = "claimed"    # 已占用，正在建稿
    DONE = "done"          # 已成稿
    ARCHIVED = "archived"  # 已归档


class RawMaterial(Base):
    """统一素材库：RSS 资讯 / 自有爆款 / 热点，三源合一。"""

    __tablename__ = "raw_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 来源标识（合规关键：区分是否自有账号内容）
    source_type: Mapped[SourceType] = mapped_column(String(20), index=True)
    source_url: Mapped[str] = mapped_column(String(1000))
    source_name: Mapped[str] = mapped_column(String(200), default="")

    # 内容
    title: Mapped[str] = mapped_column(String(500))
    raw_content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(200), default="")

    # 合规字段：是否自有账号内容（决定二次创作策略）
    own_flag: Mapped[bool] = mapped_column(Integer, default=0)

    # 互动数据（仅 own_note 有真实值，RSS 源多为 None）
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    # AI 提炼的爆款要素（二次创作时读这个，不读原文）
    extracted_elements: Mapped[dict] = mapped_column(JSON, default=dict)
    # 原创风险等级：low / medium / high
    originality_risk: Mapped[str] = mapped_column(String(20), default="low")

    # 采集去重用的内容指纹
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    topics: Mapped[list[Topic]] = relationship(back_populates="material")


class Topic(Base):
    """可执行选题：AI 从素材库转换而来。"""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    # 目标长尾词（搜索权重第一来源）
    keyword_target: Mapped[str] = mapped_column(String(200), default="", index=True)
    # 目标人群
    persona: Mapped[str] = mapped_column(String(200), default="")
    # 价值类型：情绪 / 实用 / 信息 / 经济
    value_type: Mapped[str] = mapped_column(String(20), default="实用")

    # 四条差异化规则（docs/07）：换人群/换场景/换角度/补增量
    differentiation: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[TopicStatus] = mapped_column(
        String(20), default=TopicStatus.POOLED, index=True
    )
    score: Mapped[float] = mapped_column(Float, default=0.0)

    # 溯源：来自哪条素材
    material_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_materials.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    material: Mapped[RawMaterial | None] = relationship(back_populates="topics")
    drafts: Mapped[list[Draft]] = relationship(back_populates="topic")


class Draft(Base):
    """稿件：图文或视频（MVP 只做图文）。"""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )

    # 流水线类型：image（图）/ video（视频）
    pipeline_type: Mapped[str] = mapped_column(String(20), default="image")

    title: Mapped[str] = mapped_column(String(100), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # 封面与内页（存路径/URL）
    cover_url: Mapped[str] = mapped_column(String(500), default="")
    images: Mapped[list] = mapped_column(JSON, default=list)

    # AI 声明（平台强制要求，不标识即违规）
    ai_declaration: Mapped[str] = mapped_column(String(100), default="")

    # 合规校验结果 {"spec": bool, "compliance": bool, "issues": [...]}
    validation: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    topic: Mapped[Topic | None] = relationship(back_populates="drafts")


class ToolCallLog(Base):
    """Agent 工具调用记录：审计与排障用。"""

    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class KnowledgeItem(Base):
    """知识库条目：踩坑与复盘的经验沉淀，供 Agent 检索。

    来源可以是 Obsidian 库导入，也可以是运行时记录的错误/复盘。
    检索走 `search_knowledge()`（BM25-lite 关键词打分 + 标签加权），
    不依赖向量库—— 本机量级（千条以内）下关键词检索足够，
    且零依赖、可离线、可解释。
    """

    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    # kind: pitfall 踩坑 / review 复盘 / spec 规格 / insight 经验
    kind: Mapped[str] = mapped_column(String(30), default="pitfall", index=True)
    # 四段式正文（沿用 Obsidian 的结构：为什么/怎么用/反例/关联）
    why: Mapped[str] = mapped_column(Text, default="")
    how: Mapped[str] = mapped_column(Text, default="")
    pitfall: Mapped[str] = mapped_column(Text, default="")   # 反例或边界
    related: Mapped[str] = mapped_column(Text, default="")   # 关联
    tags: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    # 使用统计：被检索到几次 —— 支持「定期回顾」挑高频条目复看
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class MistakeLog(Base):
    """错误本：运行时自动沉淀的踩坑（供定期回顾）。"""

    __tablename__ = "mistake_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene: Mapped[str] = mapped_column(String(100), default="", index=True)
    symptom: Mapped[str] = mapped_column(Text, default="")
    cause: Mapped[str] = mapped_column(Text, default="")
    fix: Mapped[str] = mapped_column(Text, default="")
    # reviewed: 是否已复盘过（定期回顾时按未回顾的排前面）
    reviewed: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
