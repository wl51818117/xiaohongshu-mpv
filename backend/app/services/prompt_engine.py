"""结构化提示词引擎（Prompt as Code）。

★ 为什么不用「一句话拼字符串」：
  原来的写法是把主题、风格、要求拼成一段中文散文。
  问题很具体——
    1. 改一个约束要翻整段字符串，找不到该改哪
    2. 不同场景（封面/内页/视频）共用同一套措辞，风格串味
    3. 无法复用：想给「商品图」换个光线，得重写整段
    4. 没法版本管理，也没法 A/B 对比
  借鉴 GitHub `freestylefly/awesome-gpt-image-2` 的 **Prompt as Code** 理念：
  把提示词拆成**可组合的原子块**，像写代码一样组装。

本模块的结构（每个字段都是一个原子块）：
    subject     画面主体（画什么）
    composition 构图与视角（怎么摆）
    lighting    光影（氛围的关键）
    material    材质与质感（决定"真实感"）
    colorpalette 色彩与色调
    typography  文字与信息区（小红书封面要留标题位）
    negatives   负面约束（禁止出现什么）
    quality     质量与渲染（8K、超清等）

用法：
    p = PromptSpec(subject=..., composition=...)
    text = p.render()# 渲染成给模型的提示词
    d = p.to_dict()             # 存成结构化 JSON，便于复用/比较
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# ── 尺寸常量（docs/02硬要求）──
COVER_W, COVER_H = 1080, 1440
ASPECT = "3:4"


# ── 可复用原子块库 ─────────────────────────────────────────
# 每个块都是「一类视觉决策」，可被不同场景复用。
# 这样跨场景保持一致的视觉语言，同时允许单点覆盖。

STYLE_BLOCKS: dict[str, dict[str, str]] = {
    # ── 视觉风格：决定整体调性 ──
    "realistic": {
        "label": "写实摄影",
        "lighting": "自然光，柔和侧逆光，浅景深虚化背景",
        "material": "真实皮肤与织物质感，细节纹理清晰",
        "color": "低饱和自然色调，干净通透",
        "quality": "高清摄影质感，8K超清，自然噪点",
    },
    "clean": {
        "label": "干净极简",
        "lighting": "均匀柔光，无硬阴影",
        "material": "哑光质感，材质平整",
        "color": "低饱和莫兰迪色系，大面积留白",
        "quality": "高清质感，画面极简无冗余",
    },
    "vivid": {
        "label": "色彩鲜明",
        "lighting": "高对比侧光，轮廓光明显",
        "material": "高饱和材质，质感强化",
        "color": "高饱和撞色，明快活泼",
        "quality": "高清质感，色彩浓郁",
    },
    "film": {
        "label": "胶片种草",
        "lighting": "窗边自然光，轻微逆光",
        "material": "胶片颗粒感，柔焦边缘",
        "color": "暖黄低对比，怀旧氛围",
        "quality": "胶片质感，轻微颗粒",
    },
    "studio": {
        "label": "电商白底",
        "lighting": "三点布光，均匀无死角",
        "material": "商品质感锐利，包装细节可辨",
        "color": "纯净白背景，色彩准确",
        "quality": "商业摄影级，锐利清晰",
    },
    "lifestyle": {
        "label": "生活场景",
        "lighting": "室内暖光，自然散射",
        "material": "生活化真实材质，有使用痕迹",
        "color": "温暖居家色调",
        "quality": "生活纪实感，自然",
    },
}

# 构图块：按画面类型给不同默认构图
COMPOSITION_BLOCKS: dict[str, dict[str, str]] = {
    "cover": {
        "label": "封面构图",
        "base": f"竖版 {ASPECT}（{COVER_W}×{COVER_H}），主体居中偏上，占画面 60% 以上",
        "negative": "杂乱背景、抢戏元素",
    },
    "inner": {
        "label": "内页构图",
        "base": f"竖版 {ASPECT}（{COVER_W}×{COVER_H}），主体居中，留出上下呼吸空间",
        "negative": "拥挤、多个主体",
    },
    "detail": {
        "label": "细节特写",
        "base": f"竖版 {ASPECT}，微距视角聚焦材质纹理，浅景深",
        "negative": "全景、远景",
    },
    "scene": {
        "label": "场景实拍",
        "base": f"竖版 {ASPECT}，人物或物品置于真实使用场景中，环境交代清楚",
        "negative": "纯白底、影棚感",
    },
    "compare": {
        "label": "对比呈现",
        "base": f"竖版 {ASPECT}，左右或上下分区对比，视觉分隔清楚",
        "negative": "混乱、无明确对比",
    },
    "infographic": {
        "label": "信息图解",
        "base": f"竖版 {ASPECT}，层次分明的图文区块，网格对齐",
        "negative": "插画风、纯装饰",
    },
}


@dataclass
class PromptSpec:
    """结构化提示词。所有字段可选，未填的块自动省略。"""

    subject: str = ""              # 画面主体（必填）
    composition: str = ""          # 构图视角
    lighting: str = ""             # 光影
    material: str = ""             # 材质
    color: str = ""                # 色彩
    typography: str = ""           # 文字与信息区
    negatives: list[str] = field(default_factory=list)   # 负面约束
    quality: str = ""              # 质量
    # 元信息（不进提示词，但随结果一起存，便于复用/比较）
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> PromptSpec:
        known = {k: v for k, v in d.items() if k in cls.__annotations__}
        return cls(**known)

    def render(self) -> str:
        """渲染成给模型的自然语言提示词。

        顺序固定（主体→构图→光影→材质→色彩→文字→质量→负面），
        这样不同场景生成的提示词结构一致，便于 A/B 对比。
        """
        parts: list[str] = []
        if self.subject:
            parts.append(self.subject.strip().rstrip("。"))
        if self.composition:
            parts.append(self.composition.strip())
        if self.lighting:
            parts.append(f"光影：{self.lighting.strip()}")
        if self.material:
            parts.append(f"材质：{self.material.strip()}")
        if self.color:
            parts.append(f"色彩：{self.color.strip()}")
        if self.typography:
            parts.append(f"文字区：{self.typography.strip()}")
        if self.quality:
            parts.append(f"质量：{self.quality.strip()}")
        if self.negatives:
            parts.append("禁止出现：" + "、".join(self.negatives))
        return "。".join(p for p in parts if p) + "。"

    def summary(self) -> dict:
        """给前端展示的字段清单（哪些块被填了）。"""
        blocks = {
            "subject": "画面主体",
            "composition": "构图",
            "lighting": "光影",
            "material": "材质",
            "color": "色彩",
            "typography": "文字区",
            "quality": "质量",
        }
        return {
            "filled": {blocks[k]: bool(getattr(self, k)) for k in blocks},
            "negatives": self.negatives,
            "meta": self.meta,
        }


# ── 小红书场景通用负面约束 ─────────────────────────────────
# 所有场景都禁止的（基于平台合规 + 已知生成缺陷）
XHS_BASE_NEGATIVES: list[str] = [
    "水印",
    "二维码",
    "联系方式",
    "文字乱码",   # AI 直接写中文必然乱码，文字要后期叠加
    "畸形手部",
    "扭曲人脸",
    "品牌 logo",   # 避免侵权
    "低分辨率",
]


# ── 场景预设 ───────────────────────────────────────────────

def cover_spec(
    title: str,
    key_points: list[str] | None = None,
    style: str = "realistic",
    core_terms: list[str] | None = None,
) -> PromptSpec:
    """封面提示词：小红书封面要在信息流里抢眼，主体突出且留标题位。"""
    st = STYLE_BLOCKS.get(style, STYLE_BLOCKS["realistic"])
    comp = COMPOSITION_BLOCKS["cover"]

    # 主体：把稿件信息点写进画面，而不是泛泛的漂亮图
    subject_bits = [f"小红书电商封面，主题「{title}」"]
    if core_terms:
        subject_bits.append(f"核心概念：{'、'.join(core_terms[:2])}")
    if key_points:
        subject_bits.append("画面传达：" + "；".join(key_points[:2]))

    return PromptSpec(
        subject="，".join(subject_bits),
        composition=comp["base"] + "；右下角预留信息区（后期叠字用），文字区域不超过画面 30%",
        lighting=st["lighting"],
        material=st["material"],
        color=st["color"],
        typography="预留干净的信息区，便于后期叠加标题文字（不在图内直接写字）",
        negatives=XHS_BASE_NEGATIVES + ["杂乱背景", "过多装饰元素"],
        quality=st["quality"],
        meta={"scene": "cover", "style": style, "style_label": st["label"]},
    )


def inner_spec(
    title: str,
    index: int,
    total: int,
    key_points: list[str] | None = None,
    style: str = "realistic",
) -> PromptSpec:
    """内页提示词：每张承载稿件的一个真实信息点（而非 6 张都用全景）。"""
    st = STYLE_BLOCKS.get(style, STYLE_BLOCKS["realistic"])

    # 按索引决定构图类型：全景→细节→场景→对比→图解→指引
    comp_keys = ["cover", "detail", "scene", "compare", "infographic", "cover"]
    comp_key = comp_keys[(index - 1) % len(comp_keys)]
    comp = COMPOSITION_BLOCKS[comp_key]

    subject_bits = [f"小红书图文内页第 {index}/{total} 张，主题「{title}」"]
    if key_points:
        subject_bits.append(f"本张要点：{key_points[(index - 1) % len(key_points)]}")

    return PromptSpec(
        subject="，".join(subject_bits),
        composition=comp["base"],
        lighting=st["lighting"],
        material=st["material"],
        color=st["color"],
        negatives=XHS_BASE_NEGATIVES + [comp["negative"]],
        quality=st["quality"],
        meta={
            "scene": "inner",
            "comp_type": comp_key,
            "comp_label": comp["label"],
            "index": index,
            "style": style,
        },
    )


def video_spec(title: str, scene: str = "static", core_terms: list[str] | None = None) -> PromptSpec:
    """图生视频运镜提示词：只描述运镜与动态（实际以图为主）。"""
    motion = {
        "rotate": "镜头缓慢环绕主体，运动幅度小，速度均匀",
        "push": "镜头缓慢推近主体，景深渐变",
        "static": "镜头基本静止，仅主体轻微动作",
        "detail": "镜头聚焦细节做微距移动，浅景深",
    }.get(scene, "镜头基本静止，仅主体轻微动作")

    subject = f"基于首帧图生成短视频，主题「{title}」"
    if core_terms:
        subject += f"，核心概念 {'、'.join(core_terms[:2])}"

    return PromptSpec(
        subject=subject,
        composition=motion,
        lighting="保持首帧光线一致，不跳变",
        material="保持首帧材质质感，不变形",
        negatives=["画面闪烁", "结构变形", "色彩跳变", "文字乱码", "画面撕裂"],
        quality="画面稳定，3-5秒，30fps",
        meta={"scene": "video", "motion": scene},
    )


# ── 风格预设（供前端展示）────────────────────────────────

def list_styles() -> list[dict]:
    """给前端风格选择器用。"""
    return [
        {"key": k, "label": v["label"], "preview": v["lighting"]}
        for k, v in STYLE_BLOCKS.items()
    ]


def list_compositions() -> list[dict]:
    return [{"key": k, "label": v["label"]} for k, v in COMPOSITION_BLOCKS.items()]
