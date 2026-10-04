"""内容规格与合规校验。

依据 docs/02-内容规格与合规基线.md 与 docs/00平台流程调研结论.md。
全部为确定性规则校验，不依赖 LLM —— 这是最容易做出真实价值���部分。

规格（小红书 2026）：
  - 标题 ≤20 字，前 8-13 字须含目标长尾词（搜索权重第一来源）
  - 正文 300-800 字，前 80 字埋词（否则被折叠）
  - 核心词自然出现 2-3 次（硬堆 5 次触发限流）
  - 标签 3-5 个，覆盖品类/场景/人群
  - AI 声明必填（不标识即违规）

合规红线：
  站外导流（微信/电话/二维码）、极限词、医疗功效、绝对化用语
"""

from __future__ import annotations

import re

# ── 规格常量 ────────────────────────────────────────────
TITLE_MAX = 20
TITLE_KEYWORD_POS = (8, 13)  # 长尾词应出现在标题的字符区间
BODY_MIN = 300
BODY_MAX = 800
BODY_KEYWORD_POS = 80  # 前 80 字须埋词
KEYWORD_MIN = 2
KEYWORD_MAX = 3  # 超过 3 次算堆砌
TAGS_MIN = 3
TAGS_MAX = 5
AI_DECLARATION_REQUIRED = True

# ── 合规黑名单 ──────────────────────────────────────────
# 站外导流是平台处罚最重的一类，单独成组
TRAFFIC_WORDS = [
    "加微信", "加vx", "加V信", "微信号", "vx", "威信", "扣1", "扣2",
    "私信获取", "私我", "扫码", "二维码", "加好友", "联系方式",
    "淘宝口令", "复制打开", "看主页", "绿泡泡", "薇信",
]

# 极限词/绝对化用语
ABSOLUTE_WORDS = [
    "全网最好", "国家级", "第一品牌", "100%有效", "永不复发",
    "绝对", "顶级", "万能", "彻底根治", "永久",
]

# 医疗功效
MEDICAL_WORDS = [
    "疗效", "治愈", "根治", "药到病除", "处方", "确诊",
    "美白丸", "医美级", "瘦脸针", "减肥药",
]

# 平台规则中点名的两个高频违规句式
INDUCEMENT_WORDS = [
    "评论区扣", "私信我", "关注领", "点赞领",
]

ALL_BLOCKLIST = {
    "站外导流": TRAFFIC_WORDS,
    "极限用语": ABSOLUTE_WORDS,
    "医疗功效": MEDICAL_WORDS,
    "诱导互动": INDUCEMENT_WORDS,
}


def _count_keyword(text: str, keyword: str) -> int:
    """统计关键词出现次数（中文按字匹配，忽略大小写）。"""
    if not keyword:
        return 0
    return text.lower().count(keyword.lower())


def _keyword_terms(keyword: str) -> list[str]:
    """把长尾词拆成可埋入的核心词。

    选题里的 keyword_target 可能是「手机银行 功能 职场人 办事效率」这种
    多词组合——整串塞进标题既超字数又读不通。
    这里取最长的 2 个词作为核心词（标题里埋不下这么多）。
    """
    if not keyword:
        return []
    terms = [t for t in re.split(r"[\s、,，/|+]+", keyword) if t]
    if not terms:
        return [keyword]
    # 取最长的两个，兼顾可读性与信息量
    terms.sort(key=len, reverse=True)
    return terms[:2]


def validate_draft(
    title: str,
    body: str,
    tags: list[str] | None = None,
    keyword: str = "",
    ai_declaration: str = "",
    pipeline_type: str = "image",
) -> dict:
    """校验稿件，返回 {passed, spec_issues, compliance_issues, score}。

    spec        规格问题（可修复的技术指标）
    compliance  合规问题（涉及违规，必须人工处理）
    """
    spec: list[str] = []
    compliance: list[str] = []

    # ── 合规：黑名单 ──
    full_text = f"{title}\n{body}\n{' '.join(tags or [])}"
    for category, words in ALL_BLOCKLIST.items():
        hit = [w for w in words if w in full_text.lower()]
        if hit:
            compliance.append(f"{category}：命中「{'、'.join(hit[:3])}」")

    # ── 合规：AI 声明 ──
    if AI_DECLARATION_REQUIRED and not ai_declaration.strip():
        compliance.append("缺少 AI 内容声明（平台强制要求，不标识即违规）")

    # ── 规格：标题 ──
    terms = _keyword_terms(keyword)
    if not title.strip():
        spec.append("标题为空")
    else:
        if len(title) > TITLE_MAX:
            spec.append(f"标题 {len(title)} 字，超出 {TITLE_MAX} 字上限")
        if terms:
            # 至少一个核心词应出现在标题里
            hit = [t for t in terms if t in title]
            if not hit:
                spec.append(
                    f"标题未包含目标长尾词的核心词（{'/'.join(terms)}）"
                )
            else:
                lo, hi = TITLE_KEYWORD_POS
                pos = min(title.find(t) for t in hit)
                # 允许"标题很短"时词出现在开头
                if pos > hi and len(title) > lo:
                    spec.append(
                        f"长尾词核心词在标题第 {pos + 1} 字，建议移到前 {lo}-{hi} 字（搜索权重来源）"
                    )

    # ── 规格：正文 ──
    body_len = len(body.strip())
    if body_len == 0:
        spec.append("正文为空")
    else:
        if body_len < BODY_MIN:
            spec.append(f"正文 {body_len} 字，低于 {BODY_MIN} 字下限")
        if body_len > BODY_MAX:
            spec.append(f"正文 {body_len} 字，超出 {BODY_MAX} 字上限")
        if terms:
            hit = [t for t in terms if t in body]
            if not hit:
                spec.append(f"正文未出现目标长尾词的核心词（{'/'.join(terms)}）")
            else:
                pos = min(body.find(t) for t in hit)
                if pos > BODY_KEYWORD_POS:
                    spec.append(
                        f"长尾词核心词在正文第 {pos + 1} 字，建议移到前 {BODY_KEYWORD_POS} 字内（否则被折叠）"
                    )
            # 核心词出现次数（取最多的那个）
            n = max(_count_keyword(body, t) for t in terms)
            if n > KEYWORD_MAX:
                spec.append(
                    f"长尾词核心词出现 {n} 次，超过 {KEYWORD_MAX} 次属堆砌，可能触发限流"
                )

    # ── 规格：标签 ──
    tag_list = tags or []
    if len(tag_list) < TAGS_MIN:
        spec.append(f"标签 {len(tag_list)} 个，少于 {TAGS_MIN} 个")
    if len(tag_list) > TAGS_MAX:
        spec.append(f"标签 {len(tag_list)} 个，超出 {TAGS_MAX} 个上限")

    # ── 规格：视频链路 ──
    if pipeline_type == "video":
        if not ai_declaration.strip():
            spec.append("视频链路同样需 AI 声明")

    # ── 评分：用于排序与提示 ──
    total = len(spec) + len(compliance)
    score = max(0, 100 - total * 15)

    return {
        "passed": not spec and not compliance,
        "spec_issues": spec,
        "compliance_issues": compliance,
        "score": score,
        "stats": {
            "title_len": len(title.strip()),
            "body_len": body_len,
            "tag_count": len(tag_list),
            "keyword_hits": (
                max(_count_keyword(body, t) for t in _keyword_terms(keyword))
                if _keyword_terms(keyword)
                else 0
            ),
        },
    }


def ai_declaration_text(pipeline_type: str = "image") -> str:
    """生成 AI 声明文案。

    平台允许的表述是「内容含 AI 生成/合成内容」，
    不要求具体说明哪部分用了 AI —— 过犹不及反而像在掩饰。
    """
    if pipeline_type == "video":
        return "本内容含 AI 生成的画面与配音"
    return "本内容含 AI 辅助生成部分"
