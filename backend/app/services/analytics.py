"""数据回流与复盘（M7 数据看板）。

闭环的最后一块：发布后的数据回来 →提炼爆文要素 → 回流选题库。
没有这块，跑得再久也是凭感觉。

★ 当前实现说明：
  数据来源设计为「手动/自动录入 + 可选 API 对接」。
  小红书创作者后台的数据需登录态，本服务不代抓（合规考虑），
  由前端录入或后续接官方 API。表结构与提炼逻辑已就绪。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Draft, RawMaterial, Topic, TopicStatus

# 快照时点（对应 docs/05 的复盘节奏）
SNAPSHOT_OFFSETS = [1, 3, 7, 14]  # 发布后第 1/3/7/14 天

# 投流判定阈值（docs/00-核心情报补充）
TUITUAN_CTR = 0.05# 薯条：封面点击率 ≥5%
TUITUAN_READ = 0.40      # 且完读率 ≥40%
JUGUANG_LIKE_COLLECT = 100  # 聚光：48h 赞藏 ≥100
JUGUANG_CTR = 0.03       # 且点击率 ≥3%


@dataclass
class MetricsInput:
    """一条笔记的表现数据。"""

    impressions: int = 0
    reads: int = 0
    likes: int = 0
    collects: int = 0
    comments: int = 0
    shares: int = 0
    follows: int = 0
    avg_read_seconds: float = 0.0
    completion_rate: float = 0.0  # 完播率（视频）
    ctr: float = 0.0# 点击率
    read_rate: float = 0.0        # 完读率

    def as_dict(self) -> dict[str, Any]:
        return {
            "impressions": self.impressions,
            "reads": self.reads,
            "likes": self.likes,
            "collects": self.collects,
            "comments": self.comments,
            "shares": self.shares,
            "follows": self.follows,
            "avg_read_seconds": self.avg_read_seconds,
            "completion_rate": round(self.completion_rate, 4),
            "ctr": round(self.ctr, 4),
            "read_rate": round(self.read_rate, 4),
        }


def compute_derived(m: MetricsInput) -> dict[str, Any]:
    """派生指标与投流建议。

    阈值全部取自 docs/00 的调研结论，不自造。

    ★ 判定顺序很关键（踩过坑）：
      必须**先看内容价值信号（收藏率）**，再看点击/完读。
      早期版本只判投流阈值，导致「点击够但收藏率低」的笔记被判为
      可投放 —— 但这类笔记投出去转化也差，白花钱。
      正确顺序：点击太低 → 改封面；收藏太低 → 补价值；都达标 → 才投。
    """
    interaction = m.likes + m.collects + m.comments + m.shares
    interaction_rate = interaction / m.reads if m.reads else 0.0
    collect_rate = m.collects / m.reads if m.reads else 0.0
    follow_rate = m.follows / m.reads if m.reads else 0.0

    # ① 点击率是硬门槛：低于 2.5% 连流量池都进不去，投什么都白费
    if m.ctr < 0.025:
        return {
            "interaction_rate": round(interaction_rate, 4),
            "collect_rate": round(collect_rate, 4),
            "follow_rate": round(follow_rate, 4),
            "advice": {
                "action": "fix_cover",
                "reason": (
                    f"点击率仅 {m.ctr:.1%}，低于 2.5% 晋级线"
                    "——优先换封面与标题，别急着投放"
                ),
            },
            "thresholds": {
                "tuituan": {"ctr": TUITUAN_CTR, "read_rate": TUITUAN_READ},
                "juguang": {"like_collect": JUGUANG_LIKE_COLLECT, "ctr": JUGUANG_CTR},
            },
        }

    # ② 收藏率是内容价值信号：太低说明「没有可复用的东西」，投了也白费
    if collect_rate < 0.03:
        return {
            "interaction_rate": round(interaction_rate, 4),
            "collect_rate": round(collect_rate, 4),
            "follow_rate": round(follow_rate, 4),
            "advice": {
                "action": "add_value",
                "reason": (
                    f"点击 {m.ctr:.1%} 达标但收藏率仅 {collect_rate:.1%}"
                    "——内容缺可复用价值，先补干货/步骤再投"
                ),
            },
            "thresholds": {
                "tuituan": {"ctr": TUITUAN_CTR, "read_rate": TUITUAN_READ},
                "juguang": {"like_collect": JUGUANG_LIKE_COLLECT, "ctr": JUGUANG_CTR},
            },
        }

    # ③ 前两项都过，再看投流阈值
    tuiluan_ok = m.ctr >= TUITUAN_CTR and m.read_rate >= TUITUAN_READ
    juguang_ok = (
        m.likes + m.collects >= JUGUANG_LIKE_COLLECT and m.ctr >= JUGUANG_CTR
    )
    if tuiluan_ok or juguang_ok:
        reason = (
            f"薯条达标（点击 {m.ctr:.1%} / 完读 {m.read_rate:.1%}）"
            if tuiluan_ok
            else f"聚光达标（赞藏 {m.likes + m.collects} / 点击 {m.ctr:.1%}）"
        )
        action = "spend"
    else:
        reason = "内容价值与点击都达标，但未达投放阈值——可小预算试投观察"
        action = "hold"

    return {
        "interaction_rate": round(interaction_rate, 4),
        "collect_rate": round(collect_rate, 4),
        "follow_rate": round(follow_rate, 4),
        "advice": {"action": action, "reason": reason},
        "thresholds": {
            "tuituan": {"ctr": TUITUAN_CTR, "read_rate": TUITUAN_READ},
            "juguang": {"like_collect": JUGUANG_LIKE_COLLECT, "ctr": JUGUANG_CTR},
        },
    }


def record_snapshot(
    db: Session, draft_id: int, metrics: MetricsInput
) -> dict[str, Any]:
    """记录一次数据快照（发布后 1/3/7/14 天）。"""
    draft = db.get(Draft, draft_id)
    if not draft:
        raise ValueError(f"稿件不存在：{draft_id}")

    history = list(draft.validation.get("metrics_history", []) if isinstance(
        draft.validation, dict
    ) else [])
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        **metrics.as_dict(),
    }
    history.append(entry)
    draft.validation = {
        **(draft.validation or {}),
        "metrics_history": history,
        "latest_metrics": entry,
        "derived": compute_derived(metrics),
    }
    db.commit()
    return draft.validation["derived"]


def extract_patterns(db: Session, limit: int = 20) -> dict[str, Any]:
    """提炼爆文要素（闭环回流的关键）。

    ★ 要素必须是「可复用的规律」，不是原文 ——
    平台对站内搬运/自我重复打击很严（相似度>60% 判抄袭）。
    """
    drafts = (
        db.query(Draft)
        .filter(Draft.title != "")
        .order_by(Draft.updated_at.desc())
        .limit(limit * 3)
        .all()
    )

    scored: list[tuple[float, Draft]] = []
    for d in drafts:
        latest = (d.validation or {}).get("latest_metrics")
        if not latest:
            continue
        # 综合分：收藏权重最高（平台最看重的复用信号），其次互动
        reads = latest.get("reads", 0) or 0
        if reads <= 0:
            continue
        score = (
            latest.get("collects", 0) * 3
            + latest.get("likes", 0)
            + latest.get("comments", 0) * 2
            + latest.get("shares", 0) * 2
            + latest.get("follows", 0) * 5
        ) / reads
        scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    # 归纳标题公式（取高频结构词）
    title_words: dict[str, int] = {}
    for _, d in top:
        for ch in d.title:
            if ch.strip():
                title_words[ch] = title_words.get(ch, 0) + 1

    return {
        "sampled": len(scored),
        "top_drafts": [
            {
                "draft_id": d.id,
                "title": d.title,
                "score": round(score, 4),
                "keyword": d.topic.keyword_target if d.topic else "",
                "persona": d.topic.persona if d.topic else "",
                "value_type": d.topic.value_type if d.topic else "",
                "length": len(d.body or ""),
                "tags": d.tags,
            }
            for score, d in top
        ],
        "value_type_dist": _dist([d.topic.value_type if d.topic else "" for _, d in top]),
        "persona_dist": _dist([d.topic.persona if d.topic else "" for _, d in top]),
        "avg_length": (
            round(sum(len(d.body or "") for _, d in top) / len(top)) if top else 0
        ),
        "note": (
            "以上是**规律**（标题结构、选题类型、人群、长度），"
            "不是可照抄的原文。复用时必须换人群/场景/角度并补新信息，"
            "否则相似度超 60% 会被判抄袭。"
        ),
    }


def _dist(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        if v:
            out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda x: x[1], reverse=True))


def comment_topics(comments: list[str], limit: int = 10) -> list[dict[str, Any]]:
    """从评论里挖选题（最省力的选题来源）。

    高频提问句式 = 未被满足的搜索需求。
    """
    signals = [
        "求推荐", "怎么买", "在哪里", "多少钱", "适合", "哪个好", "怎么选",
        "求链接", "求教程", "怎么办", "有没有", "区别",
    ]
    hits: dict[str, int] = {}
    for c in comments:
        for s in signals:
            if s in c:
                hits[s] = hits.get(s, 0) + 1
    ranked = sorted(hits.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [
        {"signal": s, "count": c, "suggested_topic": f"围绕「{s}」写一篇"}
        for s, c in ranked
    ]


def feed_topics_from_patterns(db: Session, patterns: dict[str, Any]) -> dict[str, Any]:
    """把爆文要素回流成新选题（闭环收口）。

    ★ 只生成「选题方向」，不复制原文 ——
      新选题必须是不同人群/场景/角度，且由AI 二次创作补内容。
    """
    created: list[int] = []
    specs: list[dict[str, str]] = []

    for item in patterns.get("top_drafts", [])[:5]:
        vt = item.get("value_type") or "实用"
        persona = item.get("persona") or ""
        keyword = item.get("keyword") or ""
        if not keyword:
            continue
        specs.append(
            {
                "title": f"{keyword}：换个场景的{('实操' if vt == '实用' else '解读')}（二次创作）",
                "keyword_target": keyword,
                "persona": persona,
                "value_type": vt,
            }
        )

    for spec in specs:
        exists = db.query(Topic).filter(Topic.title == spec["title"]).first()
        if exists:
            continue
        t = Topic(
            title=spec["title"][:200],
            keyword_target=spec["keyword_target"][:200],
            persona=spec["persona"][:200],
            value_type=spec["value_type"],
            differentiation=["换人群", "换场景", "换角度", "补信息增量"],
            status=TopicStatus.POOLED,
        )
        db.add(t)
        db.flush()
        created.append(t.id)

    db.commit()
    return {"created": len(created), "topic_ids": created}


def dashboard(db: Session) -> dict[str, Any]:
    """数据看板总览。"""
    total_drafts = db.query(func.count(Draft.id)).scalar() or 0
    with_metrics = 0
    passed_validation = 0
    for d in db.query(Draft).all():
        v = d.validation or {}
        if v.get("latest_metrics"):
            with_metrics += 1
        if v.get("passed"):
            passed_validation += 1

    topics_total = db.query(func.count(Topic.id)).scalar() or 0
    topics_pooled = (
        db.query(func.count(Topic.id))
        .filter(Topic.status == TopicStatus.POOLED)
        .scalar()
        or 0
    )
    materials = db.query(func.count(RawMaterial.id)).scalar() or 0

    return {
        "materials": materials,
        "topics_total": topics_total,
        "topics_pooled": topics_pooled,
        "drafts_total": total_drafts,
        "drafts_with_metrics": with_metrics,
        "drafts_passed": passed_validation,
        "snapshot_offsets_days": SNAPSHOT_OFFSETS,
        "note": (
            "metrics 需手动录入或接官方 API —— "
            "本服务不代抓小红书数据（合规考虑）"
        ),
    }
