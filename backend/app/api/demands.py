"""需求采集 API —— 让「痛点」从拍脑袋变成带证据的数据。

★ 这个模块解决的根本问题：
  原来 `Persona.concerns` 是个 JSON 字段，没有来源、没有证据、没法验证，
  只能靠 AI 填（实测三个 Persona 的 `own_words` 全是空的）。
  **不可验证的痛点 = 伪需求**，会一路污染选题、写作、投放。

★ 置信度的算法（关键设计）：
  `evidence_count`（跨来源计数）由系统**自动累加**——
  同一条痛点从 3 个不同渠道各出现一次，才算高置信度。
  人工**不能**直接填这个数，否则又会变成拍脑袋。

  为什么必须跨来源才算：
  单渠道高频 ≠ 真需求。评论区说「贵」可能是真，也可能是其它产品贵。
  但「电商差评 + 小红书评论 + 1688询盘」三处都提「克重不够」，
  这就是硬需求。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DemandSignal, Persona, Product
from app.db.session import get_db

router = APIRouter(prefix="/api/demands", tags=["demands"])

# 高置信度阈值：跨来源数
HIGH_CONFIDENCE = 3


# ── 请求模型 ────────────────────────────────────────────────

class DemandIn(BaseModel):
    """录入一条需求信号。"""

    verbatim: str = Field(..., min_length=1, description="用户原话（最值钱，别改写）")
    topic: str = Field("", description="归一化痛点标签，如「材质糙」；留空则自动归类")
    signal_type: Literal["symptom", "param", "objection", "scene"] = "symptom"
    persona: str = ""
    scene: str = ""
    product_id: int | None = None
    source: str = Field(
        "manual",
        description="xiaohongshu_comment / xiaohongshu_search / ecommerce_review / "
        "alibaba_inquiry / own_support / competitor_comment / manual",
    )
    source_ref: str = Field("", description="可复核引用：笔记标题+日期 / 差评片段 / 询盘编号")
    brand: str = Field("", description="品牌（竞品差评类必填）")
    intensity: int = Field(1, ge=1, le=3)
    note: str = ""


class DemandUpdateIn(BaseModel):
    """人工确认 / 修正。"""

    verified: bool = True
    topic: str | None = None
    note: str | None = None


# ── 痛点归一化 ──────────────────────────────────────────────
# 目的：把不同用户说的同一件事归到同一个 topic，
# 这样「材质糙」「像环保袋」「手感粗糙」会聚成一条，证据数才能累加。

_TOPIC_RULES: list[tuple[str, list[str]]] = [
    ("材质粗糙", ["粗糙", "糙", "像纸", "环保袋", "手感差", "掉絮", "掉毛", "封边硬", "薄"]),
    ("材质像无纺布", ["无纺布", "非织造", "像塑料", "塑料膜"]),
    ("闷不透气", ["闷", "不透气", "捂", "出汗", "潮", "闷热", "不吸汗"]),
    ("勒腰", ["勒", "紧绷", "勒出印", "越穿越紧", "卡腰", "腰头紧"]),
    ("卡裆掉裆", ["卡裆", "掉裆", "下滑", "滑落", "裆", "走光", "移位"]),
    ("尺码不准", ["码数", "尺码", "偏小", "偏大", "标码", "选码", "克重", "160克"]),
    ("不透光", ["透光", "透", "走光", "浅色", "透明"]),
    ("刺痒过敏", ["痒", "过敏", "刺痒", "起疹", "发红", "不适"]),
    ("有味道", ["味道", "刺鼻", "异味", "化纤味"]),
    ("包装破损", ["破损", "破了", "漏了", "脏", "不卫生"]),
    ("价格贵", ["太贵", "贵", "单价高", "不划算", "两块"]),
    ("穿几次", ["穿几次", "几次换", "能用几次", "机洗", "水洗"]),
    ("发货慢", ["发货", "几天到", "到货", "物流", "现货", "备货"]),
    ("行李占位", ["行李", "占地方", "占位", "带一包", "塞不下"]),
]


def normalize_topic(text: str) -> str:
    """把用户原话归一到痛点标签。归不了一律返回空（不硬凑）。

    ★ 规则顺序有意义：先匹配具体的（「材质像无纺布」比「材质粗糙」准），
      所以更具体的规则必须排在前面。
    """
    low = (text or "").lower()
    for topic, keys in _TOPIC_RULES:
        if any(k in low for k in keys):
            return topic
    return ""


def classify_signal_type(text: str) -> str:
    """判断信号类型。

    ★ 参数化追问（「160斤能穿吗」）的购买意向远高于泛泛的吐槽，
      必须区分开——前者的转化率是后者的数倍。
    """
    low = (text or "").lower()
    if re.search(r"[0-9０-９]+\s*(斤|码|块|元|次|天|条)|xl|xxl|均码", low):
        return "param"
    if re.search(r"能不能|可以吗|会不会|是不是|有没有|行不行", low):
        return "param"
    if re.search(r"太贵|不敢|怕|犹豫|不放心|担心", low):
        return "objection"
    return "symptom"


# ── 录入 ────────────────────────────────────────────────────

def _out(d: DemandSignal) -> dict[str, Any]:
    return {
        "id": d.id,
        "verbatim": d.verbatim,
        "topic": d.topic,
        "signal_type": d.signal_type,
        "persona": d.persona,
        "scene": d.scene,
        "product_id": d.product_id,
        "source": d.source,
        "source_ref": d.source_ref,
        "brand": d.brand,
        "intensity": d.intensity,
        "evidence_count": d.evidence_count,
        "verified": bool(d.verified),
        "note": d.note,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.post("", summary="录入一条需求信号")
def create_demand(req: DemandIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """录入需求。**证据数由系统自动累加，人工不能填。**

    机制：若同 persona+topic 已有记录，则**合并**——
      - evidence_count +1
      - source 追加（去重）
      - 保留最早的 verbatim 作为代表，同时记录新原话
    这样「同一痛点被多个渠道反复提到」会自然浮现为高置信度。
    """
    topic = req.topic or normalize_topic(req.verbatim)
    stype = req.signal_type if req.topic else classify_signal_type(req.verbatim)

    if not topic:
        # 不硬凑标签，但仍然录入（原始信息本身有价值）
        topic = "未归类"

    existing = None
    if topic != "未归类":
        # ★ 合并键只用 topic，**不包含 persona**。
        #   理由：「材质粗糙」在大码女性和差旅女性那里都出现，
        #   这是**同一条需求被两个人群印证**，证据力更强。
        #   如果按 persona 拆开，跨人群印证会失效——
        #   而跨人群出现恰恰是伪需求最有力的反证
        #   （只在一个人群里提 → 可能是那个人的特殊偏好）。
        existing = db.execute(
            select(DemandSignal)
            .where(DemandSignal.topic == topic)
            .order_by(DemandSignal.evidence_count.desc())
            .limit(1)
        ).scalar_one_or_none()

    if existing:
        # 合并：证据 +1，来源并集
        sources = {s.strip() for s in (existing.source or "").split(",") if s.strip()}
        sources.add(req.source)
        existing.source = ",".join(sorted(sources))
        existing.evidence_count += 1
        # 新原话追加到 note，保留最早的作为代表
        if req.verbatim and req.verbatim not in (existing.verbatim or ""):
            existing.note = (
                f"{existing.note}\n[+{req.source}] {req.verbatim}"
            ).strip()
        # 情绪强度取更高
        existing.intensity = max(existing.intensity, req.intensity)
        # 人群并集（同一痛点可能跨人群）
        if req.persona and req.persona not in (existing.persona or ""):
            existing.persona = ",".join(
                sorted({*(existing.persona or "").split(","), req.persona} - {""})
            )
        # 品牌追加（竞品红黑榜要用）
        if req.brand and req.brand not in (existing.brand or ""):
            existing.brand = ",".join(
                sorted({*(existing.brand or "").split(","), req.brand} - {""})
            )
        db.commit()
        db.refresh(existing)
        return {
            "ok": True, "action": "merged", "id": existing.id,
            "evidence_count": existing.evidence_count,
            "topic": existing.topic,
            "note": f"同主题已存在 {existing.evidence_count} 次出现"
            + ("（达高置信度）" if existing.evidence_count >= HIGH_CONFIDENCE else ""),
        }

    d = DemandSignal(
        verbatim=req.verbatim, topic=topic, signal_type=stype,
        persona=req.persona, scene=req.scene, product_id=req.product_id,
        source=req.source, source_ref=req.source_ref, brand=req.brand,
        intensity=req.intensity, note=req.note,
        # verified 只有手动勾选才是 1
        verified=0,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return {
        "ok": True, "action": "created", "id": d.id,
        "topic": d.topic, "signal_type": stype,
        "note": "已录入。补齐 source_ref 后人工确认（verified）才能进选题引擎"
        if not req.source_ref else "已录入（带引用）",
    }


@router.get("", summary="需求列表")
def list_demands(
    topic: str = "",
    persona: str = "",
    source: str = "",
    signal_type: str = "",
    min_evidence: int = Query(0, ge=0, le=50),
    verified_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按条件筛选需求。`min_evidence` 用于只看高置信度。"""
    stmt = select(DemandSignal)
    if topic:
        stmt = stmt.where(DemandSignal.topic == topic)
    if persona:
        stmt = stmt.where(DemandSignal.persona == persona)
    if source:
        stmt = stmt.where(DemandSignal.source.like(f"%{source}%"))
    if signal_type:
        stmt = stmt.where(DemandSignal.signal_type == signal_type)
    if min_evidence:
        stmt = stmt.where(DemandSignal.evidence_count >= min_evidence)
    if verified_only:
        stmt = stmt.where(DemandSignal.verified == 1)

    rows = list(
        db.execute(
            stmt.order_by(
                DemandSignal.evidence_count.desc(), DemandSignal.intensity.desc()
            ).limit(limit)
        ).scalars()
    )
    return {"count": len(rows), "items": [_out(r) for r in rows]}


@router.patch("/{signal_id}", summary="人工确认或修正")
def update_demand(
    signal_id: int, req: DemandUpdateIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """人工确认（verified=1）+ 可修正 topic 归类。

    ★ 为什么 `verified` 与 `evidence_count` 分开：
      证据数是**跨来源客观计数**（系统算），
      verified 是**「这条来源真实、表述准确」的主观确认**（人判）。
      两者不可互相替代——一个渠道反复提可能是真的，也可能是刷的。
    """
    d = db.get(DemandSignal, signal_id)
    if not d:
        raise HTTPException(status_code=404, detail="需求记录不存在")
    d.verified = 1 if req.verified else 0
    if req.topic:
        d.topic = req.topic
    if req.note is not None:
        d.note = req.note
    db.commit()
    db.refresh(d)
    return {"ok": True, "item": _out(d)}


@router.delete("/{signal_id}", summary="删除需求")
def delete_demand(signal_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    d = db.get(DemandSignal, signal_id)
    if not d:
        raise HTTPException(status_code=404, detail="需求记录不存在")
    db.delete(d)
    db.commit()
    return {"ok": True}


# ── 聚合与洞察 ──────────────────────────────────────────────

@router.get("/clustered", summary="按痛点聚合（这是选题引擎的输入）")
def clustered(
    min_evidence: int = Query(HIGH_CONFIDENCE, ge=0, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按 topic 聚合并给高置信度标记。

    ★ 默认 min_evidence=3：只有**跨3 个来源**印证的需求才算高置信度。
      这是防「一篇爆文带偏模型」的关键——
      单一渠道的高频可能只是噪声，交叉印证才是需求。
    """
    rows = list(
        db.execute(
            select(DemandSignal)
            .where(DemandSignal.evidence_count >= min_evidence)
            .order_by(DemandSignal.evidence_count.desc())
        ).scalars()
    )
    items = [_out(r) for r in rows]
    return {
        "count": len(items),
        "min_evidence": min_evidence,
        "high_confidence": [i for i in items if i["evidence_count"] >= HIGH_CONFIDENCE],
        "items": items,
    }


@router.get("/stats", summary="需求库概览")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    """看闭环的输入够不够。

    ★ 关键判据：`high_confidence_count`。
      低于 5 条时，选题引擎只能退回到拍脑袋模式——
      这时候应该去采集，而不是让 AI 再猜。
    """
    total = db.execute(select(func.count(DemandSignal.id))).scalar_one()
    verified = db.execute(
        select(func.count(DemandSignal.id)).where(DemandSignal.verified == 1)
    ).scalar_one()
    high = db.execute(
        select(func.count(DemandSignal.id))
        .where(DemandSignal.evidence_count >= HIGH_CONFIDENCE)
    ).scalar_one()

    by_type = dict(
        db.execute(
            select(DemandSignal.signal_type, func.count(DemandSignal.id))
            .group_by(DemandSignal.signal_type)
        ).all()
    )
    by_source = dict(
        db.execute(
            select(DemandSignal.source, func.count(DemandSignal.id))
            .group_by(DemandSignal.source)
        ).all()
    )
    by_topic = dict(
        db.execute(
            select(DemandSignal.topic, func.count(DemandSignal.id))
            .group_by(DemandSignal.topic)
            .order_by(func.count(DemandSignal.id).desc())
        ).all()
    )

    # 来源多样性：判断是否只靠单一渠道
    distinct_sources = len([s for s in by_source if s])

    ready = high >= 5
    return {
        "total": total,
        "verified": verified,
        "high_confidence": high,
        "by_type": by_type,
        "by_source": by_source,
        "top_topics": [{"topic": k, "n": v} for k, v in list(by_topic.items())[:10]],
        "distinct_sources": distinct_sources,
        # 单一来源是最危险的状态：容易把渠道偏好误当需求
        "single_source_risk": distinct_sources <= 1 and total > 0,
        "engine_ready": ready,
        "hint": (
            "需求库已可支撑选题引擎（高置信度 ≥5 条）"
            if ready
            else f"高置信度需求仅 {high} 条，选题引擎仍会依赖拍脑袋。"
                 "建议继续采集——优先补 1688 询盘与电商差评（目前最少被采集的渠道）"
        ),
    }


@router.get("/by-persona/{persona_name}", summary="按人群看需求")
def by_persona(persona_name: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """某人群的全部需求信号（用户原话优先展示）。"""
    rows = list(
        db.execute(
            select(DemandSignal)
            .where(DemandSignal.persona == persona_name)
            .order_by(DemandSignal.evidence_count.desc())
        ).scalars()
    )
    return {
        "persona": persona_name,
        "count": len(rows),
        "items": [_out(r) for r in rows],
        "own_words": [r.verbatim for r in rows if r.verbatim][:20],
    }


@router.post("/sync-personas", summary="把高置信度需求回写进 Persona")
def sync_personas(db: Session = Depends(get_db)) -> dict[str, Any]:
    """把 cross-validated 的需求同步到 `Persona.concerns`。

    ★ 这样 `Persona.concerns` 才不是拍脑袋填的——
      它的每一条都能在 `DemandSignal` 里找到证据。
      同步时按 evidence_count 降序取，且只取 verified 或高置信度的。
    """
    rows = list(
        db.execute(
            select(DemandSignal)
            .where(
                (DemandSignal.verified == 1)
                | (DemandSignal.evidence_count >= HIGH_CONFIDENCE)
            )
            .order_by(DemandSignal.evidence_count.desc())
        ).scalars()
    )

    by_persona: dict[str, list[str]] = {}
    for r in rows:
        if not r.persona or r.topic == "未归类":
            continue
        by_persona.setdefault(r.persona, [])
        if r.topic not in by_persona[r.persona]:
            by_persona[r.persona].append(r.topic)

    updated = 0
    for name, topics in by_persona.items():
        p = db.execute(
            select(Persona).where(Persona.name == name)
        ).scalar_one_or_none()
        if not p:
            continue
        # 合并：保留原有但标 unverified 的，补上新的
        existing = list(p.concerns or [])
        merged = list(dict.fromkeys([*topics, *existing]))
        p.concerns = merged
        p.own_words = [r.verbatim for r in rows if r.persona == name and r.verbatim][:20]
        updated += 1

    db.commit()
    return {
        "ok": True,
        "personas_updated": updated,
        "topics_synced": sum(len(v) for v in by_persona.values()),
        "note": "Persona.concerns 现在每条都能在需求表找到证据",
    }
