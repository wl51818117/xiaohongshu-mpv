"""素材分析器 —— 采集与选题之间的**缺失环节**。

★ 为什么必须有这一层（2026-10-05 修复）：
  原来流程是「采集 → 素材库 → 点加入选题 → 照搬标题建 Topic」。
  问题很具体：
    1. 标题**没有被分析**——它为什么爆？结构是什么？核心词在哪？
    2. 正文**没有被提炼**——用户关心什么？痛点是什么？
    3. 图片**完全没参与**——封面承载了大量信息（场景/主体/风格）
    4. `extracted_elements` 字段设计了但从没被填过，只有采集时塞了话题
    5. 于是转出来的「选题」只是**素材标题的搬运**，不是可执行选题

  修完的流程：
    采集 → **分析（标题结构/正文痛点/图片规格/话题）** → 基于分析生成可执行选题

★ 与「照搬标题」的本质区别：
  照搬：「2026年轻人兴趣消费趋势洞察」→ 选题同名
  分析后：「哪些人（25-35岁）会因为什么痛点（情绪价值/社交货币）
          关注这个话题」→ 选题是「25-35岁的人，情绪价值才是他们买的东西」
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.db.models import RawMaterial

# ── 标题结构模式库 ─────────────────────────────────────────
# 借鉴 awesome-gpt-image-2 的 Prompt as Code 思路：把「爆款」拆成可识别结构。
# 每种结构给出「识别特征」与「可迁移的公式」。

TITLE_PATTERNS: list[dict[str, Any]] = [
    {
        "key": "number_list",
        "label": "数字清单式",
        "pattern": r"\d+\s*(个|种|条|招|点|件|款|步|块|元|天|次)",
        "formula": "数字 + 类别量词（{n}个/种/条）",
        "why": "数字给出确定的信息量，降低点击成本",
    },
    {
        "key": "question",
        "label": "提问式",
        "pattern": r"[?？]|怎么|如何|为什么|是不是|该不该|要不要",
        "formula": "疑问句 + 具体场景",
        "why": "提问天然引发自我代入，点击率通常更高",
    },
    {
        "key": "anti_common",
        "label": "反常识/避坑",
        "pattern": r"别|千万|其实|并不|不一定|智商税|踩坑|翻车|后悔|避雷|误区|陷阱",
        "formula": "否定常见认知 + 揭示真相",
        "why": "损失厌恶：怕踩坑比想获得更强烈",
    },
    {
        "key": "persona",
        "label": "人群锚定",
        "pattern": r"[0-9]+\s*岁|姐妹|宝妈|学生党|打工人|职场|新手|小白|孕|产后|中老年|男生|女生|人",
        "formula": "具体人群 + 状态",
        "why": "人群锚定让目标读者「对号入座」",
    },
    {
        "key": "contrast",
        "label": "对比式",
        "pattern": r"vs|VS|对比|差在哪|区别|哪个好|不如|比.{0,3}好",
        "formula": "A vs B + 判断依据",
        "why": "对比是决策阶段的刚需内容",
    },
    {
        "key": "benefit",
        "label": "利益承诺式",
        "pattern": r"必|一定要|建议|记住|收藏|干货|指南|攻略|教程|清单|方法",
        "formula": "动作词 + 名词（建议/记住/收藏）",
        "why": "明确告诉读者「读完能得到什么」",
    },
    {
        "key": "scene",
        "label": "场景代入式",
        "pattern": r"出差|旅行|上班|通勤|约会|露营|军训|住校|经期|产后|换季|冬天|夏天|618|双11",
        "formula": "时间/场景 + 行为",
        "why": "场景越具体，搜索匹配度越高",
    },
    {
        "key": "emotion",
        "label": "情绪共鸣式",
        "pattern": r"emo|焦虑|内耗|破防|治愈|爽|太真实|谁懂|狠狠|绝了|服了|哭",
        "formula": "情绪词 + 强度词",
        "why": "情绪是转发的驱动力（分享即认同）",
    },
]


def _match_title_patterns(title: str) -> list[dict[str, Any]]:
    """识别标题用的结构。"""
    hits = []
    for p in TITLE_PATTERNS:
        if re.search(p["pattern"], title, re.I):
            hits.append(
                {
                    "key": p["key"],
                    "label": p["label"],
                    "formula": p["formula"],
                    "why": p["why"],
                }
            )
    return hits


# ── 正文信号提炼 ───────────────────────────────────────────

# 痛点信号：用户抱怨的具体表现
_PAIN_SIGNALS = {
    "闷": ["闷", "不透气", "捂", "出汗", "潮湿"],
    "勒": ["勒", "紧绷", "卡", "压痕", "勒出印"],
    "卷边/移位": ["卷边", "移位", "下滑", "走光", "掉裆"],
    "过敏/痒": ["痒", "过敏", "起疹", "红", "刺", "不适"],
    "透光": ["透", "透明", "走光"],
    "掉色/串色": ["掉色", "串色", "染色"],
    "不耐用": ["破", "破洞", "开线", "变形", "起球", "掉絮", "用一次"],
    "有味道": ["味道", "刺鼻", "异味", "化纤味"],
    "包装/卫生": ["包装", "破损", "脏", "不卫生", "漏"],
    "价格": ["贵", "太贵", "不划算", "多少钱"],
}

# 场景信号
_SCENE_SIGNALS = {
    "差旅": ["出差", "旅行", "酒店", "高铁", "飞机", "行李箱"],
    "经期": ["经期", "例假", "生理期", "侧漏"],
    "夏季": ["夏天", "夏季", "闷热", "出汗", "三伏"],
    "通勤": ["通勤", "上班", "久坐", "办公室", "地铁"],
    "军训/住校": ["军训", "住校", "宿舍", "开学"],
    "孕产": ["孕期", "产后", "孕妈", "月子"],
    "露营": ["露营", "户外", "野餐"],
}


def extract_pain_points(text: str) -> list[dict[str, Any]]:
    """从正文/标题里抽痛点信号（带原文，不改写）。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, keys in _PAIN_SIGNALS.items():
        for k in keys:
            if k in text and name not in seen:
                seen.add(name)
                # 找出包含这个词的句子，作为原文证据
                idx = text.find(k)
                snippet = text[max(0, idx - 14) : idx + len(k) + 14].strip()
                out.append({"name": name, "trigger": k, "quote": snippet})
                break
    return out


def extract_scenes(text: str) -> list[str]:
    """识别内容讲的是哪些场景。"""
    return [name for name, keys in _SCENE_SIGNALS.items() if any(k in text for k in keys)]


# ── 图片信息 ───────────────────────────────────────────────

def analyze_image(cover_path: str | None) -> dict[str, Any]:
    """分析封面图。

    ★ 诚实说明：现在没有接视觉模型，所以只做**客观规格分析**
      （尺寸/比例/是否实拍/占位判断），不做内容识别。
      要真正的「图里有什么」，需要接视觉模型 API——
      那属于生图 API 配置的范畴。
    """
    if not cover_path:
        return {
            "available": False,
            "reason": "未采集到封面图",
            "hint": "封面承载场景信息（谁在用、在什么场合用），"
                    "缺了会让选题缺一个判断维度",
        }

    try:
        from pathlib import Path

        from PIL import Image

        p = Path(__file__).resolve().parents[2] / cover_path
        if not p.exists():
            return {"available": False, "reason": f"文件不存在：{cover_path}"}

        with Image.open(p) as im:
            w, h = im.size
            mode = im.mode
        ratio = w / h if h else 0
        # 判断是否符合小红书 3:4
        target = 1080 / 1440
        is_3x4 = abs(ratio - target) < 0.03
        kb = p.stat().st_size / 1024
        return {
            "available": True,
            "width": w,
            "height": h,
            "ratio": round(ratio, 3),
            "aspect_ok": is_3x4,
            "aspect_note": (
                "符合 3:4" if is_3x4
                else f"非 3:4（当前 {ratio:.2f}），小红书信息流会裁切"
            ),
            "mode": mode,
            "size_kb": round(kb, 1),
            "hint": "仅规格分析。要识别图内容（人物/场景/文字）需接视觉模型 API",
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"分析失败：{exc}"}


# ── AI 深度分析（用内核）───────────────────────────────────

_ANALYZE_PROMPT = """你在分析一条小红书内容，目标是**提炼可复用的规律**，不是复述内容。

【标题】{title}
【正文】{body}
【话题】{topics}
【互动数据】{metrics}

请按下面的结构输出（严格用JSON，不要markdown 代码块）：

{{
  "title_analysis": {{
    "patterns": ["命中的标题结构，如「数字清单式」"],
    "core_words": ["标题里的核心搜索词，1-3 个"],
    "hook": "它用什么方式抓住注意力（一句话）"
  }},
  "content_analysis": {{
    "target_persona": "这条内容是给谁看的（具体到年龄段/身份/场景）",
    "core_pain": "它戳中的是什么痛点（一句话，用用户的说法）",
    "value_delivered": "它给了读者什么（清单/方法/情绪/信息）",
    "cta": "它怎么引导互动的"
  }},
  "reuse_advice": {{
    "borrowed": "这个内容里可以直接借鉴的**技巧**（不是内容本身）",
    "must_change": "照搬会被判同质化的部分，必须改成什么",
    "our_angle": "换成我们的商品/人群，题目应该是什么"
  }}
}}

判断标准：
- 互动高不代表内容好，可能只是选题本身热。要区分「结构可学」与「题材蹭热点」
- core_words 必须是**用户会搜的词**，不是标题里的漂亮字
- our_angle 要具体到一个可执行的题目，不要写「可以写相关内容」这种空话"""


async def analyze_with_ai(material: RawMaterial) -> dict[str, Any]:
    """用内核做深度分析。失败不阻塞流程（返回空 dict，规则分析仍在）。"""
    from app.services import ai_writer

    body = (material.raw_content or material.summary or "")[:2000]
    metrics = material.metrics or {}

    prompt = _ANALYZE_PROMPT.format(
        title=material.title or "",
        body=body or "（无正文）",
        topics="、".join((material.extracted_elements or {}).get("topics", [])) or "无",
        metrics=f"赞{metrics.get('likes', 0)} 藏{metrics.get('collects', 0)} "
                f"评{metrics.get('comments', 0)}",
    )

    try:
        text = await ai_writer.ask(prompt, session=f"analyze-{material.id}")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"AI 分析失败：{exc}"}

    # ★ 解析要宽容：小模型经常在 JSON 前后带解释文字、
    #   包在 ```json 代码块里、用全角标点、或只返回部分字段。
    #   部分结果也比「AI 分析失败」强——规则分析已经兜底，
    #   AI 能多给一点是一点。
    parsed = _extract_json(text)
    if parsed is None:
        return {"error": "AI 未返回可解析的 JSON", "raw": text[:300]}

    if parsed.get("_partial"):
        # 模型只返回了数组（多半是标题结构分析）——单独取出来用
        return {
            "title_analysis": {
                "patterns": [str(x) for x in parsed.get("items", [])][:5],
                "core_words": [],
            },
            "_partial": True,
        }

    return parsed


def _extract_json(text: str) -> dict | None:
    """从模型输出里尽最大努力抠出 JSON 对象。"""
    if not text:
        return None
    s = text.strip()

    # ① 优先 markdown 代码块
    for m in re.finditer(r"```(?:json)?\s*\n?([\s\S]*?)```", s):
        inner = m.group(1).strip()
        obj = _try_json(inner)
        if obj is not None:
            return obj

    # ② 裸对象（贪心匹配到最后一个 }）
    i = s.find("{")
    j = s.rfind("}")
    if i != -1 and j > i:
        obj = _try_json(s[i : j + 1])
        if obj is not None:
            return obj

    # ③ 全角引号/括号归一化后再试
    norm = (
        s.replace("“", '"').replace("”", '"')
        .replace("：", ":").replace("，", ",")
    )
    i = norm.find("{")
    j = norm.rfind("}")
    if i != -1 and j > i:
        return _try_json(norm[i : j + 1])
    return None


def _try_json(s: str) -> dict | None:
    """尝试解析。返回 dict，或把 list 包成 {"items": [...]}。"""
    obj = _loads(s)
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        # ★ 实测：小模型常在patterns 数组处提前收尾，
        #   返回一个字符串数组而不是完整对象。
        #   这段信息仍有价值（它对标题结构的分析比我们规则库细），
        #   所以包起来保留，而不是当错误丢弃。
        return {"items": obj, "_partial": True}
    return None


def _loads(s: str):
    """json.loads + 修尾逗号。"""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", s)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


# ── 统一入口 ───────────────────────────────────────────────

def analyze_rules(material: RawMaterial) -> dict[str, Any]:
    """规则分析（**不调模型，瞬间完成**）。

    产出：标题结构、核心词、痛点、场景、图片规格。
    这是 AI 分析的**基线**——即使模型挂了，这些也永远有。
    """
    text = f"{material.title} {material.summary or ''} {material.raw_content or ''}"
    patterns = _match_title_patterns(material.title or "")

    # 核心词：优先从话题里取（话题本身就是用户关注点）
    topics = (material.extracted_elements or {}).get("topics", [])
    core_words = [t for t in topics if t][:3]
    if not core_words:
        # 退而求其次：标题里的 2-4 字词
        core_words = re.findall(r"[\u4e00-\u9fff]{2,4}", material.title or "")[:3]

    return {
        "title_analysis": {
            "patterns": [p["label"] for p in patterns],
            "pattern_detail": patterns,
            "core_words": core_words,
        },
        "content_analysis": {
            "pains": extract_pain_points(text),
            "scenes": extract_scenes(text),
            "body_len": len(material.raw_content or ""),
        },
        "image_analysis": analyze_image(material.cover_url or ""),
        "metrics": material.metrics or {},
        "topics": topics,
        "source": "rules",  # 标明这是规则分析
    }
