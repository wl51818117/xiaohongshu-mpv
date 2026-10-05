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
    # ★ 浏览器扩展人工采集（2026-10-05）
    #   区别于爬虫：**用户主动点击**才采集当前这一页，
    #   复用用户自己的登录态，不批量、不绕过验证。
    BROWSER = "browser"


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


class Product(Base):
    """商品主数据 —— 内容生产的锚点。

    ★ 为什么加这个（2026-10 审计结论）：
      原系统的选题全部来自 RSS 新闻（充电桩、清关、BERT 词嵌入），
      **没有一条与商品有关**。内容飘在天上，无法回答
      「这篇内容带来的是赞藏还是订单」。

      加了商品表之后，选题公式才成立：
        选题 = Persona.concerns（人群痛点）
             × Product.pain_points（商品解决什么麻烦）
             × scene（场景）
      ——问题本身就是搜索词，转化意图远高于新闻。

    ★ proof_assets 是差异化地基：
      工厂实拍（车间/质检/面料微距）是 AI 生图替代不了的东西，
      也是同行抄不走的唯一壁垒。AI 生图做不出真车间。
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(60), default="", index=True)
    name: Mapped[str] = mapped_column(String(200))
    # category: underwear 内衣 / bird 活体鸟 / other
    category: Mapped[str] = mapped_column(String(30), default="underwear", index=True)
    # 价格带，如「9.9-19.9/条」——决定目标人群的价格敏感度
    price_band: Mapped[str] = mapped_column(String(60), default="")
    unit: Mapped[str] = mapped_column(String(20), default="条")
    # 工厂能给的硬卖点：面料成分、克重、工艺、起订量
    selling_points: Mapped[list] = mapped_column(JSON, default=list)
    # 这货解决什么麻烦（选题的核心输入）
    pain_points: Mapped[list] = mapped_column(JSON, default=list)
    # 适用场景：差旅/经期/夏季久坐/孕产/露营
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    # ★ 可公开的资质：CMA检测报告编号/专利号/执行标准
    #   有了它，「抗菌」这类宣称才合法（见 draft_validator.CREDENTIAL_CLAIMS）
    certs: Mapped[list] = mapped_column(JSON, default=list)
    # 实拍素材路径（车间/质检/包装/面料微距）——AI 生图替代不了
    proof_assets: Mapped[list] = mapped_column(JSON, default=list)
    # 该商品专属禁说词（叠加在全局黑名单之上）
    taboo_words: Mapped[list] = mapped_column(JSON, default=list)
    moq: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(20), default="on", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Persona(Base):
    """人群卡 —— 把 Topic.persona 从字符串升级为实体。

    ★ objections 单独列出来是有用的：
      内容一多半该在**回答反对意见**，而不是赞美商品。
      「太贵」「穿一次就破」「不敢试」「怕尺码不对」——
      这些人关心的才是内容的选题方向。
    """

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    age_range: Mapped[str] = mapped_column(String(40), default="")
    # life_stage: 学生/职场/孕产/中老年
    life_stage: Mapped[str] = mapped_column(String(30), default="", index=True)
    # 在意什么：闷、卷边、透、勒、染色、能不能反复穿
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    # 反对什么：太贵、穿一次就破、不敢试、怕尺码不对
    objections: Mapped[list] = mapped_column(JSON, default=list)
    # ★ 她们自己的说法（直接来自咨询记录，是最值钱的字段）
    own_words: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Topic(Base):
    """可执行选题：AI 从素材库转换而来，或由商品×人群×场景生成。"""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    # 目标长尾词（搜索权重第一来源）
    keyword_target: Mapped[str] = mapped_column(String(200), default="", index=True)
    # 目标人群（保留原字符串字段，同时可关联 personas 表）
    persona: Mapped[str] = mapped_column(String(200), default="")
    persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("personas.id", ondelete="SET NULL"), nullable=True
    )
    # 价值类型：情绪 / 实用 / 信息 / 经济（内容价值，非商业意图）
    value_type: Mapped[str] = mapped_column(String(20), default="实用")

    # ── 商业维度（2026-10 新增）──
    # 挂商品。资讯类选题留空。
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 场景：差旅/经期/夏季/通勤/露营/孕产
    scene: Mapped[str] = mapped_column(String(50), default="", index=True)
    # 一句话具体痛点（不是价值类型）
    pain_point: Mapped[str] = mapped_column(String(300), default="")
    # 搜索漏斗哪一段：认知 / 对比 / 决策 / 复购
    intent_stage: Mapped[str] = mapped_column(String(20), default="", index=True)
    # ★ 商业意图度 0-1。0=纯科普，1=直接带货。
    #   替代 value_type 做商业排序——value_type 答「像不像好内容」，
    #   commercial_intent 答「能不能卖货」。
    commercial_intent: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    # 这篇内容要摆出的证据（检测报告编号/工艺/车间实拍）
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    # 完整搜索句，如「出差三天带几条一次性内裤」
    search_intent: Mapped[str] = mapped_column(String(300), default="")

    # 四条差异化规则（docs/07）：换人群/换场景/换角度/补增量
    differentiation: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[TopicStatus] = mapped_column(
        String(20), default=TopicStatus.POOLED, index=True
    )
    # 综合效果分（由数据回流写入，替代原来恒为 0.0 的占位）
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
    product: Mapped[Product | None] = relationship()
    persona_ref: Mapped[Persona | None] = relationship()


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
    # ── 投票分（借鉴 ExpeL 的 rule count）──
    # 借鉴 GitHub LeapLabTHU/ExpeL 的经验库算法：
    # 规则不是"有或没有"，而是"被验证过多少次"。
    #   新增 +2 / 被再次验证 +1 / 改写 +1 / 被质疑 -1
    # score <= 0 自动退休（不物理删除，保留历史）。
    # 这样经验库能"沉淀 + 淘汰"自动循环，不会越存越乱。
    score: Mapped[int] = mapped_column(Integer, default=2, index=True)
    # status: active 在用 / retired 退休（score<=0）/ deprecated 已过时
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
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


class Inquiry(Base):
    """站内咨询记录 —— 漏斗中间那一段（2026-10 新增）。

    ★ 为什么必须有这张表：
      完整漏斗是 曝光→点击→互动→**咨询**→成交→复购。
      现有 MetricsInput 11 个字段全是流量与互动指标，
      私信/咨询/成交**一个都没有** —— 漏斗中间整段不存在。

      而「私信率」是唯一真正的购买意向信号：
        赞藏 =「不错」，私信 =「我要买」。
      缺了它，就无法判断哪类内容值得继续做。

    ★ 注意不要加「客户手机号/微信号」字段：
      小红书 2026-09-18 新规后站外导流是重罚项（最高扣 2 万，
      关联账号同罪）。私域靠**站内**店铺会员 + 群聊 + 私信完成，
      系统里存客户手机号是给自己埋雷。
    """

    __tablename__ = "inquiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 来源内容（哪篇笔记带来的咨询）
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("drafts.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # channel: dm 私信 / comment 评论 / profile 主页 / group 群聊
    channel: Mapped[str] = mapped_column(String(20), default="dm", index=True)
    # ★ 用户原话（**最有价值**）—— Persona.own_words 的来源
    raw_text: Mapped[str] = mapped_column(Text, default="")
    # 意图标签：尺码/价格/发货/质量/对比/售后/其他
    intent: Mapped[str] = mapped_column(String(40), default="", index=True)
    # stage: open 待跟进 → replied 已回复 → ordered 已成交 → lost 未成交
    stage: Mapped[str] = mapped_column(String(20), default="open", index=True)
    # 成交金额（stage=ordered 时填）
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class DemandSignal(Base):
    """需求信号（带证据）—— 2026-10 新增。

    ★ 为什么必须独立成表，而不是塞进 Persona.concerns 的 JSON：
      原来的 `concerns: JSON` 没有来源、没有证据、没法验证，
      只能靠 AI 拍脑袋填（实测三个 Persona 的 `own_words` 全是空的）。
      **不可验证的痛点 = 伪需求**，会一路污染选题、写作、投放决策。

    ★ 核心设计：
      1. `verbatim`（用户原话）是**最值钱的字段**。
         「穿了痒，洗了晒干才不痒」比「用户对材质敏感」信息量高一个量级——
         症状才能指向解法，结论不能。
      2. `source` + `source_ref` 让每条需求**可复核**。没有可复核引用的需求
         应该在 `verified=False` 状态下。
      3. `evidence_count`（跨来源计数）≥3 才算**高置信度**，
         选题引擎只用高置信度的。这是防「一篇爆文带偏模型」的机制。
      4. `signal_type` 区分信号类型——
         参数化追问（「160斤能穿吗」）的购买意向远高于泛泛的吐槽。

    ★ 故意不设`confidence` 枚举字段：
      置信度是**算出来的**（`evidence_count` 跨来源数），
      让人手填就会拍脑袋。用 `verified` 只表示「人确认过这是真的」。
    """

    __tablename__ = "demand_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ★ 用用户原话，不要改写成书面语
    verbatim: Mapped[str] = mapped_column(Text, default="")
    # 归一化后的痛点标签（便于聚合统计），如「材质糙」「码数不准」
    topic: Mapped[str] = mapped_column(String(100), default="", index=True)
    # signal_type: symptom 症状 / param 参数化追问 / objection 反对意见 / scene 场景
    signal_type: Mapped[str] = mapped_column(String(20), default="symptom", index=True)
    # 人群（对应 Persona.name）与场景
    persona: Mapped[str] = mapped_column(String(100), default="", index=True)
    scene: Mapped[str] = mapped_column(String(50), default="", index=True)
    # 关联商品（可选，同一痛点不同商品的解法可能不同）
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    # ── 证据（缺一不可）──
    # source: xiaohongshu_comment / xiaohongshu_search / ecommerce_review /
    #         alibaba_inquiry / own_support / competitor_comment / manual
    source: Mapped[str] = mapped_column(String(40), default="manual", index=True)
    # 可复核的引用：笔记标题+日期 / 差评原文片段 / 询盘编号
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    # 品牌（竞品差评类来源必填，便于做红黑榜）
    brand: Mapped[str] = mapped_column(String(60), default="")
    # 情绪强度：1 弱 / 2 中 / 3 强
    intensity: Mapped[int] = mapped_column(Integer, default=1)

    # 跨来源计数——**置信度的唯一依据**，由系统自动累加，不让人工填
    evidence_count: Mapped[int] = mapped_column(Integer, default=1, index=True)
    # verified: 人工确认过（来源真实、表述准确）
    verified: Mapped[int] = mapped_column(Integer, default=0, index=True)
    note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
