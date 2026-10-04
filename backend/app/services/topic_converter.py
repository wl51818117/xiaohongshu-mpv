"""选题转换服务：AI 把原始资讯转成可执行的小红书选题。

这是采集到选题之间的关键环节。RSS 给的是「发生了什么」，
选题必须是「用户关心什么问题」——直接搬原文是无效的。

四条转换规则（docs/00/02 调研结论）：
  1. 换视角：从行业新闻变成用户关心的问题
  2. 落到具体人群：明确谁会看
  3. 给信息增量：加原文没有的实操/对比/避坑
  4. 合规过筛：剔除医疗/金融/法律等高风险题材

四条差异化规则（docs/07二次创作）：
  换人群 / 换场景 / 换角度 / 补信息增量，至少满足 3 条

合规黑名单（小红书对以下题材极严，M1 阶段就要拦掉）：
  医疗功效、荐股理财、法律维权、政治敏感、减肥美药品效

调用方式：
  - 有内核时：走 dsh 内核（Agent 编排）
  - 无内核时：降级为启发式规则转换（保证 MVP 能独立跑通）
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import RawMaterial, SourceType, Topic, TopicStatus

# ── 合规黑名单：命中即拒绝转选题 ──────────────────────────────
COMPLIANCE_BLOCKLIST = [
    # 医疗健康功效
    "疗效", "治愈", "根治", "药到病除", "处方", "确诊", "病历",
    # 金融投资
    "荐股", "涨停", "炒股", "收益率", "保本", "理财产品推荐",
    # 法律维权
    "诉讼", "起诉", "维权索赔", "律师函",
    # 减肥美容功效
    "美白丸", "医美级", "瘦脸针", "减肥药", "快速瘦",
    # 绝对化用语（平台重点打击）
    "全网最好", "国家级", "第一品牌", "100%有效", "永不复发",
]

# ── 价值类型分类关键词 ────────────────────────────────────────
VALUE_TYPE_RULES = [
    ("情绪", ["焦虑", "内耗", "emo", "崩溃", "委屈", "共鸣", "破防"]),
    ("经济", ["省钱", "优惠", "折扣", "平价", "性价比", "亏", "捡漏"]),
    ("信息", ["原理", "机制", "科普", "数据", "报告", "研究", "趋势"]),
    ("实用", ["教程", "攻略", "方法", "技巧", "步骤", "清单", "推荐"]),
]

# ── 人群识别关键词（用于落到具体人群）─────────────────────────
PERSONA_RULES = [
    ("上班族", ["职场", "上班", "通勤", "打工人", "办公室"]),
    ("学生党", ["学生", "校园", "大学", "考试", "论文"]),
    ("宝妈", ["宝妈", "母婴", "育儿", "婴儿", "产后"]),
    ("租房人", ["租房", "合租", "房东", "押金"]),
    ("新手小白", ["小白", "新手", "入门", "第一次"]),
    ("数码爱好者", ["手机", "电脑", "数码", "芯片", "耳机"]),
]


def check_compliance(text: str) -> tuple[bool, list[str]]:
    """合规过筛：返回 (是否通过, 命中的敏感词列表)。"""
    hits = [word for word in COMPLIANCE_BLOCKLIST if word in text]
    return (not hits), hits


def classify_value_type(text: str) -> str:
    """识别价值类型：情绪/ 经济 / 信息 / 实用。"""
    for value_type, keywords in VALUE_TYPE_RULES:
        if any(k in text for k in keywords):
            return value_type
    return "实用"


def detect_persona(text: str) -> str:
    """识别目标人群。"""
    for persona, keywords in PERSONA_RULES:
        if any(k in text for k in keywords):
            return persona
    return ""


def extract_keyword(title: str, summary: str) -> str:
    """抽取候选长尾关键词。

    MVP 用启发式（取中文词组），生产可换成 LLM 抽取。
    长尾词是小红书搜索流量的入口，比标题更重要。
    """
    text = f"{title} {summary}"
    # 匹配 2-6 字的中文词组
    candidates = re.findall(r"[\u4e00-\u9fa5]{2,6}", text)
    # 过滤：太短无意义、含数字开头的
    filtered = [c for c in candidates if len(c) >= 3 and not c[0].isdigit()]
    if not filtered:
        return title[:20]
    # 取最长的几个（信息量通常更大）
    filtered.sort(key=len, reverse=True)
    return filtered[0][:20]


def build_differentiation(material: RawMaterial) -> list[str]:
    """生成差异化维度（docs/07 四条规则）。

    MVP 基于素材属性推断维度；生产由 LLM 规划。
    """
    text = f"{material.title} {material.summary}"
    dims: list[str] = []

    persona = detect_persona(text)
    if persona:
        dims.append("换人群")

    if material.source_type == SourceType.RSS:
        # 资讯类内容天然需要补增量，否则只是新闻搬运
        dims.append("补信息增量")

    # 资讯 → 实用化的视角转换
    if any(k in text for k in ["发布", "上线", "推出", "宣布", "融资", "升级"]):
        dims.append("换角度")

    if not dims:
        dims.append("补信息增量")

    return dims


def convert_to_topic(db: Session, material: RawMaterial) -> tuple[Topic | None, str]:
    """把素材转成选题。返回 (topic, reason)。

    合规不通过则返回 (None, 原因)，不进选题库。
    """
    # 规则一：合规过筛（硬闸）
    passed, hits = check_compliance(f"{material.title} {material.summary}")
    if not passed:
        return None, f"命中合规黑名单：{', '.join(hits[:3])}"

    text = f"{material.title} {material.summary}"

    topic = Topic(
        title=material.title[:200],
        keyword_target=extract_keyword(material.title, material.summary),
        persona=detect_persona(text),
        value_type=classify_value_type(text),
        differentiation=build_differentiation(material),
        status=TopicStatus.POOLED,
        material_id=material.id,
        score=0.0,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    return topic, "转换成功"


def convert_pending(db: Session, limit: int = 20) -> dict:
    """批量转换未转换的素材为选题。"""
    materials = (
        db.query(RawMaterial)
        .filter(~RawMaterial.topics.any())
        .order_by(RawMaterial.fetched_at.desc())
        .limit(limit)
        .all()
    )

    converted = 0
    rejected = 0
    reasons: list[str] = []

    for material in materials:
        topic, reason = convert_to_topic(db, material)
        if topic:
            converted += 1
        else:
            rejected += 1
            reasons.append(reason)

    return {
        "processed": len(materials),
        "converted": converted,
        "rejected": rejected,
        "reject_reasons": reasons[:5],
    }
