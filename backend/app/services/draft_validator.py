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

# ── 合规分类 ────────────────────────────────────────────
# ★★ 方向性修正（2026-10，必读）：
#   原实现把「私信获取」「私我」「看主页」放进 TRAFFIC_WORDS 当违规。
#   这是**过严且方向反了**——小红书 2026-09-18《交易导流违规管理细则》
#   把监管扩到「群聊/私聊/商品信息/售后链路」，但官方给出的**合规替代写法**
#   恰恰是「引导用户私信、看主页合集、用企业号官方电话组件，
#   不要引导跳出平台」。
#
#   也就是说：站内私信是 9/18 新规后**唯一安全的主成交渠道**，
#   而旧规则把唯一合规的路也拦掉了，等于什么都不许做。
#
#   所以拆成两组：
#     CTA_ALLOWED —— 站内引导，合规，只做提示
#     CTA_BLOCKED —— 真正违规：把人导向站外、或用谐音变体规避
TRAFFIC_WORDS = [
    # ★ 站外触点（这是真正被罚的）
    "加微信", "加vx", "加V信", "微信号", "vx", "威信", "薇信", "绿泡泡",
    "扣1", "扣2", "扫码", "扫二维码", "二维码",
    "淘宝口令", "复制打开", "tb口令", "某宝",
    "手机号", "电话联系", "加好友", "联系方式",
    # 谐音/变体规避（平台明确「变形表达同样识别」）
    "加v信", "v x", "V信", "扣one", "扣一", "滴我",
    # 诱导跳出平台
    "详情见主页", "主页有联系方式", "看主页拿",
]

# 站内引导：合规，**只提示不阻断**
CTA_ALLOWED = [
    "私信", "评论区", "主页", "合集", "店铺", "小黄车",
    "关注我", "收藏", "点赞",
]

# 平台规则中点名的诱导互动句式（诱导互动 ≠ 站内引导，仍属违规）
INDUCEMENT_WORDS = [
    "评论区扣", "关注领", "点赞领", "转发抽奖",
    "评论区回复领取", "扣666",
]

# 极限词/绝对化用语
ABSOLUTE_WORDS = [
    "全网最好", "国家级", "第一品牌", "100%有效", "永不复发",
    "绝对", "顶级", "万能", "彻底根治", "永久",
    "最好", "最优", "最便宜", "天花板", "天花板级",
]

# 医疗功效
MEDICAL_WORDS = [
    "疗效", "治愈", "根治", "药到病除", "处方", "确诊",
    "美白丸", "医美级", "医用平替", "瘦脸针", "减肥药", "消炎", "杀菌",
]

ALL_BLOCKLIST = {
    "站外导流": TRAFFIC_WORDS,
    "极限用语": ABSOLUTE_WORDS,
    "医疗功效": MEDICAL_WORDS,
    "诱导互动": INDUCEMENT_WORDS,
}

# ── 资质宣称（需证据背书，不能一刀切禁）──────────────────
# ★ 这些不是「敏感词」，而是「没有检测报告就不能说」的宣称。
#   一刀切禁词会误伤——「抗菌率 99%」有 CMA 报告支撑就是合法的。
#   所以做成**条件校验**：稿件命中了宣称，但关联商品没有对应资质 → 判不合规。
#   （资质数据来自 Product.certs，见 models.Product）
CREDENTIAL_CLAIMS = {
    "抗菌": ["抗菌率", "抑菌率", "抗菌检测"],
    "抑菌": ["抗菌率", "抑菌率", "抑菌检测"],
    "A类": ["A类检测", "A类报告"],
    "母婴级": ["母婴检测", "母婴报告"],
    "孕产妇": ["孕产检测", "孕产报告"],
    "无菌": ["无菌检测", "灭菌报告", "EO灭菌"],
    "医用": ["医疗器械注册", "医用报告"],
}


def check_credential_claims(text: str, certs: list[str] | None) -> list[str]:
    """资质宣称的条件校验。

    逻辑：命中宣称 → 查 certs 里有没有能背书的检测项 → 没有则判不合规。
    这是从「关键词黑名单」到「证据链管理」的升级：
    有报告就大胆说并展示证据，没报告一律不说。
    """
    if not certs:
        certs = []
    joined = "、".join(certs)
    issues: list[str] = []
    for claim, evidences in CREDENTIAL_CLAIMS.items():
        if claim not in text:
            continue
        if not any(ev in joined for ev in evidences):
            issues.append(
                f"宣称「{claim}」但无对应检测报告支撑"
                f"（需 {'/'.join(evidences)} 之一）——虚假背书风险"
            )
    return issues


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
    certs: list[str] | None = None,
) -> dict:
    """校验稿件，返回 {passed, spec_issues, compliance_issues, cta_hints, score}。

    spec        规格问题（可修复的技术指标）
    compliance  合规问题（涉及违规，必须人工处理）
    cta_hints   站内引导提示（**合规**，只是提醒确认写法）
    """
    spec: list[str] = []
    compliance: list[str] = []
    cta_hints: list[str] = []

    # ── 合规：黑名单 ──
    full_text = f"{title}\n{body}\n{' '.join(tags or [])}"
    for category, words in ALL_BLOCKLIST.items():
        hit = [w for w in words if w in full_text.lower()]
        if hit:
            compliance.append(f"{category}：命中「{'、'.join(hit[:3])}」")

    # ── 合规：资质宣称（条件校验，不是黑名单）──
    for msg in check_credential_claims(full_text, certs):
        compliance.append(f"资质宣称：{msg}")

    # ── 合规：AI 声明 ──
    if AI_DECLARATION_REQUIRED and not ai_declaration.strip():
        compliance.append("缺少 AI 内容声明（平台强制要求，不标识即违规）")

    # ── 提示：站内引导（合规，不阻断）──
    cta_hit = [w for w in CTA_ALLOWED if w in full_text]
    if cta_hit:
        cta_hints.append(
            f"检测到站内引导「{'、'.join(cta_hit[:3])}」——"
            f"这是小红书 9/18 新规后**唯一安全的主成交渠道**，合规。"
            f"请确保是引导站内（私信/主页合集/店铺），而非导向站外。"
        )

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
        "cta_hints": cta_hints,
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
