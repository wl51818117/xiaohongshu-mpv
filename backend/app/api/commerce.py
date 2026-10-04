"""商品主数据 / 人群卡 / 咨询台API。

★ 这个模块解决审计发现的根本问题：
  原来 14 条选题里只有 1 条和商品有关，其余是充电桩/清关/BERT 词嵌入。
  选题来自 RSS 新闻 → 内容飘在天上 → 无法回答「这篇带来赞藏还是订单」。

  加了商品主数据后，选题公式才成立：
    选题 = Persona.concerns × Product.pain_points × scene
  ——问题本身就是搜索词，用户主动搜来，转化意图远高于新闻。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Inquiry, Persona, Product, Topic
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["commerce"])


# ── 请求模型 ────────────────────────────────────────────────

class ProductIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    sku: str = ""
    category: str = "underwear"
    price_band: str = ""
    unit: str = "条"
    selling_points: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    certs: list[str] = Field(default_factory=list)
    proof_assets: list[str] = Field(default_factory=list)
    taboo_words: list[str] = Field(default_factory=list)
    moq: str = ""
    notes: str = ""


class PersonaIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    age_range: str = ""
    life_stage: str = ""
    concerns: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    own_words: list[str] = Field(default_factory=list)


class InquiryIn(BaseModel):
    raw_text: str = Field(..., min_length=1, description="用户原话")
    channel: Literal["dm", "comment", "profile", "group"] = "dm"
    topic_id: int | None = None
    draft_id: int | None = None
    product_id: int | None = None
    intent: str = ""
    note: str = ""


class InquiryStageIn(BaseModel):
    stage: Literal["open", "replied", "ordered", "lost"]
    amount: float = 0.0
    note: str = ""


class SuggestIn(BaseModel):
    product_id: int
    limit: int = Field(10, ge=1, le=40)
    intent_stages: list[str] = Field(default_factory=list)


# ── 商品 ────────────────────────────────────────────────────

def _product_out(p: Product, db: Session) -> dict[str, Any]:
    topic_cnt = db.execute(
        select(func.count(Topic.id)).where(Topic.product_id == p.id)
    ).scalar_one()
    return {
        "id": p.id, "sku": p.sku, "name": p.name, "category": p.category,
        "price_band": p.price_band, "unit": p.unit,
        "selling_points": p.selling_points or [],
        "pain_points": p.pain_points or [],
        "scenes": p.scenes or [],
        "certs": p.certs or [],
        "proof_assets": p.proof_assets or [],
        "taboo_words": p.taboo_words or [],
        "moq": p.moq, "status": p.status, "notes": p.notes,
        "topic_count": topic_cnt,
    }


@router.get("/products", summary="商品列表")
def list_products(
    category: str = "", status: str = "", db: Session = Depends(get_db)
) -> dict[str, Any]:
    stmt = select(Product)
    if category:
        stmt = stmt.where(Product.category == category)
    if status:
        stmt = stmt.where(Product.status == status)
    rows = list(db.execute(stmt.order_by(Product.id.desc())).scalars())
    return {"count": len(rows), "items": [_product_out(p, db) for p in rows]}


@router.post("/products", summary="新增商品")
def create_product(payload: ProductIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = Product(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"ok": True, "id": p.id, "item": _product_out(p, db)}


@router.put("/products/{product_id}", summary="更新商品")
def update_product(
    product_id: int, payload: ProductIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="商品不存在")
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return {"ok": True, "item": _product_out(p, db)}


@router.delete("/products/{product_id}", summary="删除商品")
def delete_product(product_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="商品不存在")
    used = db.execute(
        select(func.count(Topic.id)).where(Topic.product_id == product_id)
    ).scalar_one()
    if used:
        # 有关联选题时不硬删，改为下架——否则会留下孤儿选题
        p.status = "off"
        db.commit()
        return {"ok": True, "soft_deleted": True,
                "note": f"该商品有 {used} 条关联选题，已改为下架而非删除"}
    db.delete(p)
    db.commit()
    return {"ok": True}


# ── 人群卡 ──────────────────────────────────────────────────

@router.get("/personas", summary="人群卡列表")
def list_personas(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = list(
        db.execute(select(Persona).order_by(Persona.id)).scalars()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id, "name": r.name, "age_range": r.age_range,
                "life_stage": r.life_stage,
                "concerns": r.concerns or [], "objections": r.objections or [],
                "own_words": r.own_words or [],
            }
            for r in rows
        ],
    }


@router.post("/personas", summary="新增人群卡")
def create_persona(payload: PersonaIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    exists = db.execute(
        select(Persona).where(Persona.name == payload.name)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail=f"人群「{payload.name}」已存在")
    p = Persona(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"ok": True, "id": p.id}


@router.put("/personas/{persona_id}", summary="更新人群卡")
def update_persona(
    persona_id: int, payload: PersonaIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    p = db.get(Persona, persona_id)
    if not p:
        raise HTTPException(status_code=404, detail="人群卡不存在")
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return {"ok": True}


# ── 选题建议：Persona × pain_points × scenes ────────────────

# 各意图阶段的选题模板（问题本身就是搜索词）
_INTENT_TEMPLATES = {
    "认知": ("{scene}{persona}怎么挑{product_word}", 0.5,
             "「{concern}」到底怎么一回事？"),
    "对比": ("{product_word}怎么挑，差别在哪", 0.7,
             "{concern}——贵的和便宜的差在哪？"),
    "决策": ("{scene}{persona}买{product_word}，先问清这几件事", 0.9,
             "买{product_word}前必须知道的 3 件事（{concern}最容易踩坑）"),
    "复购": ("{product_word}用多久换一次才卫生", 0.6,
             "一条{product_word}穿几次就该换？"),
}

_SCENE_WORDS = {
    "差旅": "出差", "经期": "经期", "夏季": "夏天", "通勤": "通勤",
    "露营": "露营", "孕产": "孕期", "旅行": "旅行", "经久": "日常",
}

# ★ 人群 × 场景的适配表：不是所有人群都关心所有场景。
#   「差旅职场女性」关心的是 差旅/通勤/旅行，不是 经期/孕产。
#   笛卡尔积会产出「差旅职场女性经期怎么选」这种语义错乱的组合。
_PERSONA_SCENES: dict[str, list[str]] = {
    "差旅": ["差旅", "通勤", "旅行"],
    "出差": ["差旅", "通勤", "旅行"],
    "旅行": ["差旅", "旅行", "露营"],
    "经期": ["经期", "通勤", "夏季"],
    "孕期": ["孕产", "经期"],
    "产": ["孕产", "经期"],
    "学生": ["通勤", "夏季", "旅行"],
    "大码": ["夏季", "通勤", "差旅"],
    "中老年": ["差旅", "通勤"],
    "健身": ["夏季", "通勤"],
    "露营": ["露营", "旅行", "夏季"],
}

# 品类英文 → 中文展示词（用户搜的是中文）
_CATEGORY_WORDS = {
    "underwear": "内裤",
    "bird": "活体鸟",
    "other": "产品",
}


def _scenes_for(persona_name: str, product_scenes: list[str]) -> list[str]:
    """取「该人群关心的场景」∩「商品支持的场景」。"""
    allowed: list[str] = []
    for key, scenes in _PERSONA_SCENES.items():
        if key in (persona_name or ""):
            allowed = scenes
            break
    if not allowed:
        # 没匹配到就用商品自己的场景
        return list(product_scenes)
    inter = [s for s in product_scenes if s in allowed]
    return inter or list(product_scenes[:2])


@router.post("/topics/suggest", summary="按商品×人群×场景生成候选选题")
def suggest_topics(req: SuggestIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """生成选题候选。

    ★ 与 RSS 转换的本质区别：
      RSS 转换问「发生了什么」，产出「月广州海关监」这种滑窗碎片；
      这里问「目标用户在这个场景会搜什么」，产出的是**完整搜索句**。

    公式：Persona.concerns × Product.pain_points × scenes × intent_stage
    其中场景做**人群适配过滤**（差旅人群不与经期场景组合）。
    """
    product = db.get(Product, req.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if not product.pain_points or not product.scenes:
        raise HTTPException(
            status_code=400,
            detail="该商品的 pain_points 或 scenes 为空，无法生成选题。"
            "请先补全——它们是选题的输入",
        )

    personas = list(db.execute(select(Persona)).scalars())
    if not personas:
        # 没有人群卡时给一个默认占位，让功能仍可用
        personas = [_DEFAULT_PERSONA]

    # 已经用过的选题，避免重复建议
    existing_kw = {
        t.keyword_target
        for t in db.execute(select(Topic)).scalars()
        if t.keyword_target
    }

    out: list[dict[str, Any]] = []
    stages = req.intent_stages or ["认知", "对比", "决策", "复购"]
    # ★ 用中文展示词，不用英文 category —— 用户搜的是中文
    product_word = _CATEGORY_WORDS.get(product.category or "", product.name[:4])
    pains = product.pain_points or []

    for ps in personas:
        concerns = ps.concerns or pains
        # ★ 场景做人群适配，而不是全笛卡尔积
        scenes = _scenes_for(ps.name or "", product.scenes or [])
        for scene in scenes:
            for stage in stages:
                tpl, intent, body_tpl = _INTENT_TEMPLATES[stage]
                for concern in (concerns[:2] or pains[:1]):
                    # 人群名去掉「女性/男性」等后缀做标题更短
                    pname = (ps.name or "").replace("女性", "").replace("男性", "")
                    scene_word = _SCENE_WORDS.get(scene, scene)
                    # ★ 去掉场景与人群名的重复：「差旅职场」+「出差」→ 只留一个
                    if pname and scene_word and scene_word in pname:
                        scene_prefix = ""
                    elif pname.startswith(scene_word) or pname.startswith(scene):
                        scene_prefix = ""
                    else:
                        scene_prefix = scene_word
                    kw = tpl.format(
                        persona=pname,
                        scene=scene_prefix,
                        product_word=product_word,
                        concern=concern,
                    )
                    kw = re.sub(r"\s+", "", kw).strip()[:20]
                    if not kw or kw in existing_kw or any(o["keyword"] == kw for o in out):
                        continue
                    out.append({
                        "keyword": kw,
                        "search_intent": kw,
                        "title": kw,
                        "persona": ps.name,
                        "persona_id": getattr(ps, "id", None),
                        "scene": scene,
                        "pain_point": concern,
                        "intent_stage": stage,
                        "commercial_intent": intent,
                        "product_id": product.id,
                        "evidence": (product.certs or [])[:2],
                        "body_outline": body_tpl.format(
                            persona=ps.name, concern=concern, product_word=product_word
                        ),
                    })
                    if len(out) >= req.limit:
                        return {"ok": True, "count": len(out), "items": out}

    return {"ok": True, "count": len(out), "items": out}


class _DefaultPersona:
    """无人群卡时的占位，避免功能不可用。"""
    id = None
    name = ""
    concerns: list[str] = []


_DEFAULT_PERSONA = _DefaultPersona()


# ── 咨询台 ──────────────────────────────────────────────────

@router.get("/inquiries", summary="咨询列表")
def list_inquiries(
    stage: str = "", intent: str = "",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Inquiry).order_by(Inquiry.created_at.desc())
    if stage:
        stmt = stmt.where(Inquiry.stage == stage)
    if intent:
        stmt = stmt.where(Inquiry.intent == intent)
    rows = list(db.execute(stmt.limit(limit)).scalars())
    return {"count": len(rows), "items": [_inquiry_out(r) for r in rows]}


def _inquiry_out(r: Inquiry) -> dict[str, Any]:
    return {
        "id": r.id, "topic_id": r.topic_id, "draft_id": r.draft_id,
        "product_id": r.product_id, "channel": r.channel,
        "raw_text": r.raw_text, "intent": r.intent, "stage": r.stage,
        "amount": r.amount, "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# 意图识别信号词（内衣/日用品场景，非通用词表）
_INTENT_SIGNALS = {
    "尺码": ["尺码", "码数", "几码", "xl", "xxl", "均码", "身高", "体重", "胖", "瘦",
             "勒", "卷边", "穿多大"],
    "价格": ["多少钱", "价格", "贵", "便宜", "优惠", "包邮", "单价", "几块", "多少钱一条"],
    "发货": ["发货", "几天", "到货", "物流", "顺丰", "快递", "现货", "什么时候能", "发不发"],
    "质量": ["质量", "手感", "厚", "薄", "透气", "会不会破", "起球", "掉色", "闷",
             # ★「能穿几次」是一次性用品最高频的问题，原信号词里没有
             "穿几次", "能用几次", "几次换", "能穿几天", "透不透", "会不会漏"],
    "对比": ["和", "区别", "哪个好", "比", "vs", "推荐", "哪个划算"],
    "售后": ["退", "换", "过敏", "不舒服", "副作用"],
    "库存": ["有货", "缺货", "还有吗", "什么时候补", "现货吗"],
    # ★ 检测报告：内衣类高频，且直接决定「抗菌」能不能说
    "资质": ["检测", "报告", "cma", "sgs", "认证", "执行标准", "符合国标", "gb"],
}


def classify_intent(text: str) -> str:
    """从用户原话识别意图标签。

    ★ 原 `analytics.comment_topics` 的信号词是「求推荐/多少钱/适合」，
      通用电商词；内衣场景真正会问的「能穿几次」「160斤选什么码」
      「包邮吗」一个都没有 —— 导致挖出来的选题全是泛泛的。
    """
    low = (text or "").lower()
    best, best_hits = "", 0
    for intent, signals in _INTENT_SIGNALS.items():
        hits = sum(1 for s in signals if s in low)
        if hits > best_hits:
            best, best_hits = intent, hits
    return best


@router.post("/inquiries", summary="记录一条咨询")
def create_inquiry(req: InquiryIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    intent = req.intent or classify_intent(req.raw_text)
    item = Inquiry(
        raw_text=req.raw_text, channel=req.channel, intent=intent,
        topic_id=req.topic_id, draft_id=req.draft_id,
        product_id=req.product_id, note=req.note,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"ok": True, "id": item.id, "intent": intent, "item": _inquiry_out(item)}


@router.patch("/inquiries/{inquiry_id}", summary="更新咨询阶段")
def update_inquiry(
    inquiry_id: int, payload: InquiryStageIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    r = db.get(Inquiry, inquiry_id)
    if not r:
        raise HTTPException(status_code=404, detail="咨询记录不存在")
    r.stage = payload.stage
    r.amount = payload.amount
    if payload.note:
        r.note = payload.note
    db.commit()
    db.refresh(r)
    return {"ok": True, "item": _inquiry_out(r)}


@router.get("/inquiries/stats", summary="咨询漏斗统计")
def inquiry_stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    """★ 私信率是唯一真正的购买意向信号（赞藏=不错，私信=我要买）。

    缺了这段，就无法判断「哪类内容值得继续做」。
    """
    total = db.execute(select(func.count(Inquiry.id))).scalar_one()
    by_stage = dict(
        db.execute(
            select(Inquiry.stage, func.count(Inquiry.id)).group_by(Inquiry.stage)
        ).all()
    )
    by_intent = dict(
        db.execute(
            select(Inquiry.intent, func.count(Inquiry.id))
            .group_by(Inquiry.intent)
        ).all()
    )
    ordered = by_stage.get("ordered", 0)
    gmv = db.execute(
        select(func.coalesce(func.sum(Inquiry.amount), 0.0))
    ).scalar_one()

    # 咨询里反复出现的原话片段（供Persona.own_words 补充）
    from collections import Counter
    import re as _re

    texts = [
        r.raw_text for r in db.execute(select(Inquiry)).scalars() if r.raw_text
    ]
    frags: Counter = Counter()
    for t in texts:
        for seg in _re.split(r"[，,。？?！!、\s]+", t):
            seg = seg.strip()
            if 3 <= len(seg) <= 12:
                frags[seg] += 1

    return {
        "total": total,
        "by_stage": by_stage,
        "by_intent": by_intent,
        "ordered": ordered,
        "gmv": float(gmv or 0),
        # 咨询→成交率
        "close_rate": round(ordered / total, 4) if total else 0.0,
        # 复购候选词（用户自己怎么说，最值钱）
        "top_words": [{"text": w, "n": n} for w, n in frags.most_common(10)],
    }
