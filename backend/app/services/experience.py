"""经验沉淀服务：把生成结果与校验结论写回知识库。

★ 为什么要单独一个模块：
  「持续增强」不是只有检索（注入），还得有**沉淀**——
  每次生成后把「什么有效、什么被拒、为什么」记下来，
  下次生成才能避开同样的错。

借鉴：
  - ExpeL（LeapLabTHU）：用「一条成功 + 一条失败」对照抽取规则
  - Generative Agents：重要性累积到阈值时触发反思
  - ADR：被否决的方案永不删除（保留反模式）

关键设计：
  1. **只沉淀通过校验的**，被拒的也要记（附拒绝原因）——
     失败案例信息量往往比成功案例大
  2. 规则必须**脱离具体案例**（ExpeL 的原话：
     "rules should be GENERALLY APPLICABLE"），
     否则经验库会退化成日志
  3. 沉淀要**幂等**，同一结论重复沉淀只是加权，不会堆重复条目
"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.services import knowledge as kb


# ── 标题经验 ────────────────────────────────────────────────

def record_title_outcome(
    topic: str,
    keyword: str,
    persona: str,
    accepted: list[str],
    rejected: list[dict] | None = None,
) -> dict:
    """记录一轮标题生成的结果（成功 + 失败对照）。

    借鉴 ExpeL 的 create_rules()：拿成功/失败对照提炼规则。
    这里做简化——不调 LLM抽取，直接把「校验器的判断」结构化落库，
    因为校验器（score_title）本身就是确定性的规则引擎，
    它的判断已经比 LLM 抽取更可靠。
    """
    rejected = rejected or []
    out: dict = {"ok": True, "saved": 0}

    db = SessionLocal()
    try:
        # ① 成功样本：记录这个选题下哪种公式被采纳
        if accepted:
            top = accepted[0]
            title = f"标题公式在「{persona or '通用人群'}」场景有效：{_formula_of(top)}"
            r = kb.apply_vote(
                db, title=title, action="add",
                why=f"选题「{topic[:40]}」下，采纳的标题是：{top}",
                how=f"可用公式：{_formula_of(top)}。核心词「{keyword}」放在前 10 字内，"
                    f"总长 {len(top)} 字。",
                tags=f"标题公式,{persona or '通用'}",
            )
            out["saved"] += 1
            out["title_rule"] = r

        # ② 失败样本：把被拒的原因沉淀成规则（失败信息量更大）
        for rej in rejected[:3]:
            t = (rej.get("title") or "").strip()
            issues = rej.get("issues") or []
            if not t or not issues:
                continue
            issue_text = "、".join(str(i) for i in issues)
            title = f"标题避免：{issue_text}"
            r = kb.apply_vote(
                db, title=title, action="add",
                why=f"实例「{t[:30]}」被校验器拒绝：{issue_text}",
                how=f"生成后自检 {', '.join(str(i) for i in issues)}；"
                    f"校验器是确定性规则，不满足就重写。",
                pitfall=f"不合格实例：{t}",
                tags=f"标题校验,{keyword or '通用'}",
            )
            out["saved"] += 1

    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = str(exc)
    finally:
        db.close()
    return out


def _formula_of(title: str) -> str:
    """从标题反推用了哪种爆款公式（用于按类目沉淀经验）。"""
    import re

    if re.search(r"[0-9０-９]+\s*(个|点|招|条|项)", title):
        return "数字+结果"
    if re.search(r"[?？]", title):
        return "提问代入"
    if re.search(r"(别|千万|其实|并不|不一定|智商税)", title):
        return "反常识 / 避坑"
    if re.search(r"(通勤|出差|旅行|约会|居家|运动|夏天|冬天|秋冬|春夏)", title):
        return "场景代入"
    if re.search(r"(姐妹|打工人|新手|小白|宝妈|学生|职场人|[0-9]+岁)", title):
        return "人群+痛点"
    if re.search(r"(清单|大全|盘点|合集)", title):
        return "清单合集"
    return "陈述式"


# ── 正文/稿件经验 ───────────────────────────────────────────

def record_draft_outcome(
    title: str,
    keyword: str,
    persona: str,
    passed: bool,
    issues: list[str] | None = None,
    body_len: int = 0,
) -> dict:
    """记录稿件校验结果。

    通过 → 记为成功样本（可复用的高分写法）
    不通过 → 记为失败样本（附具体问题）
    """
    issues = issues or []
    db = SessionLocal()
    try:
        if passed:
            r = kb.apply_vote(
                db,
                title=f"稿件结构在「{persona or '通用人群'}」场景通过校验",
                action="agree",
                why=f"实例标题「{title[:30]}」，正文 {body_len} 字，含核心词「{keyword}」",
                how=f"标题 ≤20 字、核心词前 10 字、正文 300-800 字、"
                    f"标签 3-5 个，四项全过。",
                tags=f"稿件规格,{persona or '通用'}",
            )
        else:
            issue_text = "、".join(str(i) for i in issues[:4])
            r = kb.apply_vote(
                db,
                title=f"稿件避免：{issue_text[:60]}",
                action="add",
                why=f"实例「{title[:30]}」正文 {body_len} 字，校验不通过：{issue_text}",
                how=f"提交前跑 draft_validator 自检，"
                    f"重点检查：{issue_text}",
                pitfall=issue_text,
                tags=f"稿件校验,{keyword or '通用'}",
            )
        return {"ok": True, "result": r}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()


# ── 定期回顾（自动审查低分条目）───────────────────────────

def audit_low_score(threshold: int = 0, limit: int = 10) -> dict:
    """退休长期低分的经验条目。

    借鉴 Generative Agents 的 importance_trigger：
    当库变大时触发一次批量审查，而不是无限增长。
    """
    db = SessionLocal()
    try:
        from sqlalchemy import func, select

        from app.db.models import KnowledgeItem

        total = db.execute(
            select(func.count(KnowledgeItem.id))
            .where(KnowledgeItem.status == "active")
        ).scalar_one()
        if total <= 30:
            return {"ok": True, "skipped": True, "reason": f"库内 {total} 条，未达审查阈值 30"}

        rows = list(
            db.execute(
                select(KnowledgeItem)
                .where(KnowledgeItem.status == "active")
                .order_by(KnowledgeItem.score.asc(), KnowledgeItem.hit_count.asc())
                .limit(limit)
            ).scalars()
        )
        retired = []
        for r in rows:
            if r.score <= threshold:
                r.status = "retired"
                retired.append(r.title)
        db.commit()
        return {"ok": True, "total": total, "retired": len(retired),
                "retired_titles": retired[:5]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()
