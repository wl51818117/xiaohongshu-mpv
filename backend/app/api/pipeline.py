"""主流程 API：RSS 采集 -> 选题转换 -> 选题库。

MVP 只实现这一条最小可运行主流程（剥离素材生成、发布等非必需功能）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import RawMaterial, SourceType, Topic, TopicStatus
from app.db.session import get_db
from app.services import rss_collector, topic_converter

router = APIRouter(prefix="/api", tags=["pipeline"])


# ── 请求/响应模型 ───────────────────────────────────────────
class FeedIn(BaseModel):
    """自定义 RSS 源。"""

    name: str = Field(..., examples=["机器之心"])
    url: str = Field(..., examples=["https://www.jiqizhixin.com/rss"])
    category: str = "综合"

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        """★ 安全（2026-10）：RSS URL 由服务端抓取，原来无任何校验，
        可打内网/元数据服务（SSRF）。"""
        from app.core.url_guard import UnsafeUrl, assert_safe_url

        try:
            return assert_safe_url(v)
        except UnsafeUrl as exc:
            raise ValueError(f"RSS 地址不安全：{exc}") from exc


class CollectRequest(BaseModel):
    feeds: list[FeedIn] | None = None
    limit_per_feed: int = Field(10, ge=1, le=50)
    preset: str | None = Field(
        None, description="赛道预设 key，见 GET /api/pipeline/feeds"
    )


class TopicOut(BaseModel):
    """选题输出。"""

    id: int
    title: str
    keyword_target: str
    persona: str
    value_type: str
    differentiation: list[str]
    status: str
    source_name: str = ""


# ── 主流程三个环节 ─────────────────────────────────────────
@router.get("/pipeline/status", summary="查看各环节状态")
def pipeline_status(db: Session = Depends(get_db)) -> dict:
    """一眼看清流水线状态。"""
    total_materials = db.query(func.count(RawMaterial.id)).scalar() or 0
    total_topics = db.query(func.count(Topic.id)).scalar() or 0
    unconverted = (
        db.query(func.count(RawMaterial.id))
        .filter(~RawMaterial.topics.any())
        .scalar()
        or 0
    )
    pooled = (
        db.query(func.count(Topic.id))
        .filter(Topic.status == TopicStatus.POOLED)
        .scalar()
        or 0
    )
    return {
        "materials": total_materials,
        "topics": total_topics,
        "unconverted_materials": unconverted,
        "available_topics": pooled,
    }


@router.get("/pipeline/feeds", summary="赛道预设列表（兼容旧路径）")
def list_feeds() -> dict:
    """返回可用的赛道预设，供前端下拉选择。

    已合并到 /api/feeds（设置里可视化编辑），此路径保留兼容。
    """
    from app.api.feeds import list_feeds as _new_list_feeds

    return _new_list_feeds()


@router.post("/pipeline/collect", summary="环节一：RSS 采集入库")
def collect_rss(req: CollectRequest, db: Session = Depends(get_db)) -> dict:
    """从 RSS 源抓取资讯，去重后入库。

    不传 feeds 时用 preset 指定的赛道源；都不传则用第一个预设。
    """
    feeds = None
    if req.feeds:
        feeds = [
            rss_collector.FeedConfig(
                name=f.name, url=f.url, category=f.category
            )
            for f in req.feeds
        ]
    result = rss_collector.collect_rss(
        db,
        feeds=feeds,
        limit_per_feed=req.limit_per_feed,
        preset=req.preset,
    )
    return {"ok": True, **result}


@router.post("/pipeline/convert", summary="环节二：素材转选题")
async def convert_topics(
    limit: int = Query(20, ge=1, le=100),
    extract_keyword: bool = Query(True, description="是否用 AI 抽取长尾词"),
    db: Session = Depends(get_db),
) -> dict:
    """把素材经合规过筛后转成可执行选题。

    ★ 关键词改由 AI 抽取（2026-10）：正则切不出词，会产出
      「月广州海关监」这类滑窗碎片，进而污染稿件标题、写作简报、
      校验器整条链路。AI 不可用时宁可留空待人工填。
    """
    keywords: dict[int, str] = {}
    ai_failed = 0

    if extract_keyword:
        # 先取待转换素材（与 service 内部同条件）
        materials = (
            db.query(RawMaterial)
            .filter(~RawMaterial.topics.any())
            .order_by(RawMaterial.fetched_at.desc())
            .limit(limit)
            .all()
        )
        for m in materials:
            try:
                keywords[m.id] = await topic_converter.ai_keyword(m.title, m.summary)
            except Exception:  # noqa: BLE001
                ai_failed += 1

    result = topic_converter.convert_pending(db, limit=limit, keywords=keywords)
    if ai_failed:
        result["ai_failed"] = ai_failed
        result["note"] = (
            f"{ai_failed} 条素材的长尾词抽取失败，关键词留空需人工填写"
        )
    return {"ok": True, **result}


@router.get("/topics", summary="选题库列表")
def list_topics(
    status: str | None = Query(None, description="按状态过滤"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """查看选题库。"""
    query = db.query(Topic).join(
        RawMaterial, Topic.material_id == RawMaterial.id, isouter=True
    ).order_by(Topic.created_at.desc())
    if status:
        query = query.filter(Topic.status == status)
    rows = query.limit(limit).all()

    items = [
        TopicOut(
            id=t.id,
            title=t.title,
            keyword_target=t.keyword_target,
            persona=t.persona,
            value_type=t.value_type,
            differentiation=t.differentiation,
            status=t.status.value if hasattr(t.status, "value") else str(t.status),
            source_name=(t.material.source_name if t.material else ""),
        )
        for t in rows
    ]
    return {"count": len(items), "items": [i.model_dump() for i in items]}


@router.get("/materials", summary="素材库列表")
def list_materials(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """查看已采集素材。"""
    rows = (
        db.query(RawMaterial)
        .order_by(RawMaterial.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": m.id,
                "source_name": m.source_name,
                "source_type": (
                    m.source_type.value
                    if hasattr(m.source_type, "value")
                    else str(m.source_type)
                ),
                "title": m.title,
                "own_flag": bool(m.own_flag),
                "has_topic": bool(m.topics),
                # ★ 补上这些字段：原来只回标题+来源，导致
                #   「采集了但看不到内容」——数据其实在库里，只是没返回。
                "summary": (m.summary or "")[:300],
                "body_len": len(m.raw_content or ""),
                "cover_url": m.cover_url or "",
                "author": m.author or "",
                "metrics": m.metrics or {},
                "topics": (m.extracted_elements or {}).get("topics", []),
                "originality_risk": m.originality_risk or "low",
                "source_url": m.source_url or "",
            }
            for m in rows
        ],
    }


@router.get("/health", summary="健康检查")
def health() -> dict:
    return {"ok": True}


# ══════════════════════════════════════════════════════════════
# 选题管理：删除 + 从素材直接转选题
# ══════════════════════════════════════════════════════════════

class MaterialToTopicIn(BaseModel):
    """从素材直接建选题（用户在素材库点「加入选题」时用）。"""

    material_id: int
    title: str = Field("", description="自定义选题标题，留空则用分析结果生成")
    keyword: str = Field("", description="目标长尾词，留空则从分析结果取")
    persona: str = ""
    value_type: str = "实用"
    # ★ 是否调用 AI 深度分析（会慢一些，但能给出可借鉴的规律）
    use_ai: bool = Field(True, description="关闭则只用规则分析（瞬时）")


def _merge_ai(rules: dict, ai: dict) -> dict:
    """把 AI 分析结果并入规则分析（AI 优先，规则兜底）。

    ★ 允许 AI 只返回部分字段——小模型常只给标题结构那一块，
      剩下的用规则结果补。**部分结果比「AI 失败」有用得多**。
    """
    if ai.get("error"):
        return rules

    out = dict(rules)
    out["ai"] = {k: v for k, v in ai.items() if k != "error"}
    out["ai_partial"] = bool(ai.get("_partial"))
    out["source"] = "rules+ai"

    ai_title = ai.get("title_analysis") or {}
    if ai_title:
        out.setdefault("title_analysis", {})
        # AI 的结构识别通常比规则库细，两个都留着
        if ai_title.get("patterns"):
            out["title_analysis"]["ai_patterns"] = ai_title["patterns"]
        if ai_title.get("hook"):
            out["title_analysis"]["ai_hook"] = ai_title["hook"]
        if ai_title.get("core_words"):
            out["title_analysis"]["core_words"] = ai_title["core_words"]

    ai_content = ai.get("content_analysis") or {}
    if ai_content:
        ca = out.setdefault("content_analysis", {})
        for src, dst in (
            ("target_persona", "ai_target_persona"),
            ("core_pain", "ai_core_pain"),
            ("value_delivered", "ai_value"),
            ("cta", "ai_cta"),
        ):
            if ai_content.get(src):
                ca[dst] = ai_content[src]

    if ai.get("reuse_advice"):
        out["reuse_advice"] = ai["reuse_advice"]
    return out


def _derive_topic_fields(
    analysis: dict, mat, payload: "MaterialToTopicIn"
) -> dict[str, str]:
    """基于分析结果生成选题的三个核心字段。

    ★ 与「照搬标题」的区别：
      照搬 → 选题 = 素材标题（同质化风险高，且不是我们的人群/商品）
      这里 → 从分析里取「目标人群 + 痛点 + 场景」重新组织

    优先级：AI 的 our_angle > 规则推导 > 素材原标题（兜底）
    """
    ta = analysis.get("title_analysis") or {}
    ca = analysis.get("content_analysis") or {}
    reuse = analysis.get("reuse_advice") or {}

    # 标题：AI 建议的切入角度 > 规则拼装 > 原标题
    title = (reuse.get("our_angle") or "").strip()
    if not title:
        persona = (
            (ca.get("ai_target_persona") or "").strip()
            or _guess_persona(ca, analysis)
        )
        pains = ca.get("pains") or []
        scenes = ca.get("scenes") or []
        scene = scenes[0] if scenes else ""
        core = (ta.get("core_words") or [])[:1]
        core_txt = core[0] if core else ""

        # ★ 拼接必须读得通。原实现「{scene}怎么{pain}」会造出
        #   「露营怎么勒」这种病句——「勒」是症状不是疑问对象。
        #   改为「场景 + 核心词 + 疑问式后缀」，只拼能构成疑问的成分。
        if scene and core_txt:
            title = f"{scene}{core_txt}怎么挑"
        elif core_txt:
            title = f"{core_txt}怎么选"
        elif scene:
            title = f"{scene}场景怎么选"
        elif pains:
            # 有痛点无场景：退到痛点做「避坑」式，仍是可执行题目
            title = f"{pains[0]['name']}别忽视"
        elif persona:
            title = f"{persona}关心的选购问题"

    # 关键词：**AI 识别的核心搜索词优先**，其次规则提取，最后话题兜底。
    # ★ 话题不等于搜索词：话题「兴趣消费」是内容分类，
    #   用户搜的是「露营装备怎么选」这种具体短语。
    keyword = ""
    ai_words = ta.get("core_words") or []
    rule_words = ta.get("core_words") or []
    topics = analysis.get("topics") or []

    for w in (*ai_words, *rule_words):
        w = str(w).strip()
        # 长尾搜索词通常 2-6 字
        if 2 <= len(w) <= 6:
            keyword = w
            break
    if not keyword and topics:
        # 兜底用话题，但**标注来源**，让人知道这不是真正的搜索词
        keyword = str(topics[0])[:6]

    # 人群
    persona = (
        (payload.persona or "").strip()
        or (ca.get("ai_target_persona") or "").strip()[:200]
        or _guess_persona(ca, analysis)
    )

    return {
        "title": (title or mat.title or "")[:500],
        "keyword": keyword[:200],
        "persona": persona,
    }


def _guess_persona(ca: dict, analysis: dict) -> str:
    """从场景/痛点推一个人群描述（没有 AI 结论时的兜底）。"""
    scenes = ca.get("scenes") or []
    pains = ca.get("pains") or []
    if scenes and pains:
        return f"有「{scenes[0]}」需求、被{pains[0]['name']}困扰的人"
    if scenes:
        return f"{scenes[0]}场景下的用户"
    return ""


@router.post("/materials/{material_id}/to-topic", summary="素材直接转选题")
async def material_to_topic(
    material_id: int,
    payload: MaterialToTopicIn,
    db: Session = Depends(get_db),
) -> dict:
    """把一条素材转成**可执行选题**。

    ★★ 2026-10-05 重构（之前是流程错误）：
      原来这里直接 `标题 = 素材标题`，等于把素材标题搬运一遍——
      标题没分析、正文没提炼、图片完全没参与，
      `extracted_elements` 字段设计了但从没被填过。
      转出来的东西**没有二次创作价值**。

      现在改成：采集 → **分析** → 基于分析生成选题。
      分析包含：
        1. 标题用了什么结构（数字式/提问式/反常识…）
        2. 核心搜索词是什么
        3. 正文里有哪些**痛点信号**（带原文）
        4. 讲的是哪些场景
        5. 封面图的规格是否符合 3:4
        6. （可选）AI 深度分析：可借鉴什么、必须改什么、我们该怎么切入
    """
    from app.db.models import RawMaterial
    from app.services import material_analyzer, topic_converter

    mat = db.get(RawMaterial, material_id)
    if not mat:
        raise HTTPException(status_code=404, detail="素材不存在")

    text = f"{mat.title} {mat.summary or ''}"

    # 合规预检：违规素材不允许转选题
    passed, hits = topic_converter.check_compliance(text)
    if not passed:
        raise HTTPException(
            status_code=400,
            detail=f"素材命中合规黑名单（{'、'.join(hits[:3])}），不能转选题",
        )

    # ── 第1 步：规则分析（瞬时，永远可用）──
    analysis = material_analyzer.analyze_rules(mat)

    # ── 第 2 步：AI 深度分析（可选，失败不阻塞）──
    ai_result: dict = {}
    if payload.use_ai:
        ai_result = await material_analyzer.analyze_with_ai(mat)
        if ai_result.get("error"):
            # 不让 AI 失败毁掉整个流程，规则分析已经够用
            ai_result = {"error": ai_result["error"]}
        else:
            # AI 结果合并进 analysis
            analysis = _merge_ai(analysis, ai_result)

    # 存回素材，形成可复用的资产（下次不用重复分析）
    elements = dict(mat.extracted_elements or {})
    elements["analysis"] = analysis
    mat.extracted_elements = elements
    db.commit()

    # ── 第 3 步：基于分析生成选题（不是照搬标题）──
    gen = _derive_topic_fields(analysis, mat, payload)
    title = (payload.title or "").strip() or gen["title"]
    keyword = (payload.keyword or "").strip() or gen["keyword"]
    persona = (payload.persona or "").strip() or gen["persona"]

    # ── 去重：优先按素材 ID 判（最准：同一素材不该转两次）──
    existing = (
        db.query(Topic)
        .filter(Topic.material_id == mat.id)
        .first()
    )
    if existing:
        return {
            "ok": True, "created": False,
            "id": existing.id, "title": existing.title,
            "reason": f"该素材已转成选题 #{existing.id}，不重复创建",
        }

    # 其次按「标题 + 关键词」判，且**两者都不能为空**——
    # 空关键词会让所有素材互撞（实测踩过：提示「已在选题库」但其实是误判）
    if title and keyword:
        dup = (
            db.query(Topic)
            .filter(Topic.title == title, Topic.keyword_target == keyword)
            .first()
        )
        if dup:
            return {
                "ok": True, "created": False,
                "id": dup.id, "title": dup.title,
                "reason": f"库中已有相同标题与关键词的选题 #{dup.id}",
            }

    # 差异化：优先用分析结果（比关键词匹配准得多）
    pains = (analysis.get("content_analysis") or {}).get("pains") or []
    scenes = (analysis.get("content_analysis") or {}).get("scenes") or []
    diffs: list[str] = []
    if pains:
        diffs.append(f"改痛点角度（原文痛点：{pains[0]['name']}）")
    if scenes:
        diffs.append(f"换场景（原文场景：{scenes[0]}）")
    if getattr(mat, "source_type", "") == SourceType.BROWSER:
        # 浏览器采的必须是他人内容 → 必须重写，不能照搬
        diffs.append("全文重写（他人内容，仅借鉴结构）")
    if not diffs:
        diffs = topic_converter.build_differentiation(mat)

    t = Topic(
        title=title[:500],
        keyword_target=keyword[:200],
        persona=(persona or topic_converter.detect_persona(text) or "")[:200],
        value_type=payload.value_type,
        differentiation=diffs,
        material_id=mat.id,
        status=TopicStatus.POOLED,
        # 浏览器采来的是他人内容 → 二次创作原创风险高，标在选题上提醒
        originality_risk=(
            "high" if getattr(mat, "source_type", "") == SourceType.BROWSER else "low"
        ),
        # 把分析结论带进选题，供稿件阶段直接用（不用重新分析）
        pain_point=(pains[0]["name"] if pains else "")[:300],
        scene=(scenes[0] if scenes else "")[:50],
        evidence=analysis.get("topics", [])[:3],
    )
    db.add(t)
    db.commit()
    db.refresh(t)

    result = {
        "ok": True,
        "created": True,
        "id": t.id,
        "title": t.title,
        "keyword": t.keyword_target,
        "persona": t.persona,
        "differentiation": diffs,
        # ★ 把分析结论返回给前端，让用户看到「为什么这么转」
        "analysis": {
            "title_patterns": (analysis.get("title_analysis") or {}).get("patterns", []),
            "core_words": (analysis.get("title_analysis") or {}).get("core_words", []),
            "hook": (
                (analysis.get("title_analysis") or {}).get("ai_hook")
                or next(
                    (
                        p.get("why")
                        for p in (analysis.get("title_analysis") or {})
                        .get("pattern_detail", [])
                    ),
                    "",
                )
            ),
            "pains": pains[:3],
            "scenes": scenes,
            "ai_target_persona": (analysis.get("content_analysis") or {}).get(
                "ai_target_persona", ""
            ),
            "ai_core_pain": (analysis.get("content_analysis") or {}).get(
                "ai_core_pain", ""
            ),
            "image": analysis.get("image_analysis", {}),
            "reuse_advice": analysis.get("reuse_advice", {}),
            "source": analysis.get("source", "rules"),
        },
    }
    if not keyword:
        # 明确告诉调用方：选题建了，但缺关键词，必须补
        result["warning"] = (
            "选题已创建，但**没有长尾词**。空关键词的稿件无法通过校验，"
            "请到选题库补上核心词（如「一次性内裤 差旅」）。"
        )
    if analysis.get("image_analysis", {}).get("available") is False:
        result.setdefault("hints", []).append(
            "未采集到封面图。封面承载「谁在用、什么场景」，"
            "缺了会让选题少一个判断维度——建议在浏览器里重采一次。"
        )
    return result


@router.get("/materials/{material_id}/analysis", summary="查看素材分析结果")
def get_analysis(material_id: int, db: Session = Depends(get_db)) -> dict:
    """查看某条素材的分析结果（不含重新分析，只读已存的）。"""
    from app.db.models import RawMaterial

    mat = db.get(RawMaterial, material_id)
    if not mat:
        raise HTTPException(status_code=404, detail="素材不存在")
    analysis = (mat.extracted_elements or {}).get("analysis")
    if not analysis:
        return {
            "ok": True,
            "analyzed": False,
            "hint": "这条素材还没分析过。点「加入选题」时会自动分析，"
            "或调 POST /api/materials/{id}/analyze",
        }
    return {"ok": True, "analyzed": True, "analysis": analysis}


@router.post("/materials/{material_id}/analyze", summary="单独分析素材（不建选题）")
async def analyze_only(
    material_id: int, db: Session = Depends(get_db)
) -> dict:
    """只分析不建选题——用户想先看看「系统从这条素材里看出了什么」。"""
    from app.db.models import RawMaterial
    from app.services import material_analyzer

    mat = db.get(RawMaterial, material_id)
    if not mat:
        raise HTTPException(status_code=404, detail="素材不存在")

    analysis = material_analyzer.analyze_rules(mat)
    ai_result = await material_analyzer.analyze_with_ai(mat)
    if not ai_result.get("error"):
        analysis = _merge_ai(analysis, ai_result)

    elements = dict(mat.extracted_elements or {})
    elements["analysis"] = analysis
    mat.extracted_elements = elements
    db.commit()

    return {
        "ok": True,
        "material_id": mat.id,
        "title": mat.title,
        "analysis": analysis,
    }


@router.delete("/topics/{topic_id}", summary="删除选题")
def delete_topic(topic_id: int, db: Session = Depends(get_db)) -> dict:
    """删除选题。若该选题已有稿件，一并删除稿件（避免孤儿数据）。"""
    from app.db.models import Draft

    t = db.get(Topic, topic_id)
    if not t:
        raise HTTPException(status_code=404, detail="选题不存在")

    drafts = db.query(Draft).filter(Draft.topic_id == topic_id).all()
    for d in drafts:
        db.delete(d)
    db.delete(t)
    db.commit()

    return {"ok": True, "deleted_topic": topic_id, "deleted_drafts": len(drafts)}
