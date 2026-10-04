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


# ── 关键词抽取 ────────────────────────────────────────────

# 常见虚词/无意义片段：出现在词组开头或结尾就说明截错了
_STOP_HEAD = re.compile(
    r"^(的|了|是|在|和|与|为|从|到|有|被|将|把|就|也|都|还|又|很|更|最|不|没|"
    r"上|下|中|个|之|其|此|该|这|那|我|你|他|她|们|及|或|但|而|则|于|对|为)"
)
_STOP_TAIL = re.compile(
    r"(的|了|是|在|和|与|为|从|到|有|被|将|把|就|也|都|还|又|很|更|最|不|没|"
    r"上|下|中|个|之|其|此|该|这|那|我|你|他|她|们|及|或|但|而|则|于|对|为|"
    r"们|者|地|得|着|过|来|去|出|进|第|一|二|三|四|五|六|七|八|九|十)$"
)

# 常见专有名词前缀：这些词开头的片段多半是专名，不是搜索词
_PROPER_NOUNS = {
    "有限公司", "股份", "集团", "科技", "网络", "信息", "发展", "有限公司",
    "记者", "报道", "消息", "表示", "认为", "指出", "宣布", "发布",
}


def _clean_fragment(frag: str) -> str:
    """把机械截断的片段清洗成可读词组；不行就返回空串。"""
    s = frag.strip()
    # 反复剥离头尾虚词
    for _ in range(3):
        before = s
        s = _STOP_HEAD.sub("", s)
        s = _STOP_TAIL.sub("", s)
        if s == before:
            break
    if len(s) < 3:
        return ""
    if s in _PROPER_NOUNS:
        return ""
    # 纯数字或数字+单位开头
    if re.match(r"^\d", s):
        return ""
    return s


def _readable(frag: str) -> bool:
    """判断一个词组是否「像人话」——给 AI 抽取结果做兜底过滤。"""
    if not frag or len(frag) < 3 or len(frag) > 20:
        return False
    if re.match(r"^\d", frag):
        return False
    # 连续 4 个以上相同字符（截断错乱的典型特征，如「路新路新」）
    if re.search(r"(.)\1{3,}", frag):
        return False
    # 必须含实词，不能全是虚词
    if _STOP_TAIL.sub("", frag) == "":
        return False
    return True


def extract_keyword(title: str, summary: str) -> str:
    """抽取候选长尾关键词（**纯兜底，宁可留空**）。

    ★★踩坑记录（这段历史不要删）：
      v1 用 `re.findall(r"[\u4e00-\u9fa5]{2,6}", text)` + 按长度降序取第一个。
      产出「月广州海关监」「天高速公路新」——垃圾词的源头。

      v2 以为问题在「没剥虚词」，于是加了停用词清洗。
      实测**完全无效**：「月广州海关监管广州国」照样出来。
      因为根子在于：**`{2,6}` 定长匹配根本不是分词，它切的是任意位置的
      6 字滑窗**。「月广州海关监管广州国」正好是标题第 3-12 字的滑窗。
      虚词清洗只能剥掉「的/了/在」，剥不掉「滑窗」这个事实本身。

      所以 v3 直接放弃正则路线：正则切不出词，只有语义模型能。
      这个函数现在只负责「AI 不可用时不要污染下游」——
      **抽不出就留空，绝不用碎片兜底**。空关键词用户看得见会改，
      碎片关键词用户看不见，会一路污染到稿件、简报、校验器。
    """
    return ""


async def ai_keyword(title: str, summary: str, session: str | None = None) -> str:
    """用 AI 抽取长尾词（主路径）。

    新闻标题与搜索词语域不同：「国庆假期高速公路充电近300万次」是**发生了什么**，
    「国庆高速充电排队」才是**用户会搜什么**。这类语义转换截词做不到。
    """
    from app.services import ai_writer

    # 不用 f-string：里面的 {summary[:200]} 会被误解析。
    prompt = (
        "从下面这条资讯里，抽出一个**小红书用户会主动搜索**的长尾关键词。\n"
        "\n"
        f"【资讯标题】{title}\n"
        f"【简介】{(summary or '')[:200]}\n"
        "\n"
        "要求：\n"
        "1. 输出**1 个**关键词，4-12 个字\n"
        "2. 必须是「用户会搜什么」的**搜索意图短语**，不是资讯标题的截断\n"
        "3. 站在**目标读者**的角度：他会主动搜什么词来找这类内容\n"
        "4. 不要出现公司名、人名、地名专有名词\n"
        "5. 不要出现「的」「了」「在」等虚词开头或结尾\n"
        "\n"
        "【正例】（资讯标题 → 搜索词）\n"
        "「国庆假期前3天高速公路新能源汽车充电近300万次」→「高速充电排队」\n"
        "「前9月广州海关监管广州国际港中欧班列」→「跨境电商 物流清关」\n"
        "\n"
        "【反例】\n"
        "「月广州海关监」← 机械截断，不可读\n"
        "「天高速公路新」← 机械截断，不可读\n"
        "「2026年最新动态」← 太泛，没有搜索价值\n"
        "\n"
        "只输出关键词本身，不要解释、不要引号。"
    )

    try:
        text = await ai_writer.ask(prompt, session=session)
    except Exception:  # noqa: BLE001
        return extract_keyword(title, summary)  # AI 不可用 → 降级到启发式

    kw = re.sub(r"[\s\"'“”。，,.、：:；;！!？?（）()【】\[\]]", "", text)
    kw = kw.strip()
    if not _readable(kw):
        return extract_keyword(title, summary)
    return kw[:20]


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


def convert_to_topic(
    db: Session, material: RawMaterial, keyword: str | None = None
) -> tuple[Topic | None, str]:
    """把素材转成选题。返回 (topic, reason)。

    合规不通过则返回 (None, 原因)，不进选题库。

    ★ keyword 形参：调用方（pipeline API）应先跑 `await ai_keyword()` 把
      搜索词抽出来传进来。这里不用再自己抽——正则切不出词，且抽错会污染
      下游全部环节（稿件标题、写作简报、校验器），库里 4 篇稿件就是这么废的。
      没传就走启发式兜底（当前恒返回空串 = 留待人工填）。
    """
    # 规则一：合规过筛（硬闸）
    passed, hits = check_compliance(f"{material.title} {material.summary}")
    if not passed:
        return None, f"命中合规黑名单：{', '.join(hits[:3])}"

    text = f"{material.title} {material.summary}"
    kw = keyword if keyword is not None else extract_keyword(material.title, material.summary)

    topic = Topic(
        title=material.title[:200],
        keyword_target=kw,
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

    reason = "转换成功"
    if not kw:
        #★ 明确告知：关键词待人工填，而不是让用户以为转好了
        reason = "转换成功（⚠️ 未抽出长尾词，需人工填写；空词无法通过稿件校验）"
    return topic, reason


def convert_pending(
    db: Session, limit: int = 20, keywords: dict[int, str] | None = None
) -> dict:
    """批量转换未转换的素材为选题。

    ★ keywords: `{material_id: 长尾词}`，由调用方先跑 `ai_keyword()` 得到。
      没有这个参数时关键词会留空（见 convert_to_topic 里的说明）。
    """
    materials = (
        db.query(RawMaterial)
        .filter(~RawMaterial.topics.any())
        .order_by(RawMaterial.fetched_at.desc())
        .limit(limit)
        .all()
    )

    keywords = keywords or {}
    converted = 0
    rejected = 0
    no_keyword = 0
    reasons: list[str] = []

    for material in materials:
        topic, reason = convert_to_topic(
            db, material, keyword=keywords.get(material.id)
        )
        if topic:
            converted += 1
            if not topic.keyword_target:
                no_keyword += 1
        else:
            rejected += 1
        reasons.append(reason)

    return {
        "processed": len(materials),
        "converted": converted,
        "rejected": rejected,
        "no_keyword": no_keyword,
        "reject_reasons": reasons[:5],
    }
