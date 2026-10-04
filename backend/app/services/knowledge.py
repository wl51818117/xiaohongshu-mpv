"""知识库服务（RAG）：踩坑与复盘经验的沉淀与检索。

★ 为什么不用向量库：
  本机量级（千条以内）下，**关键词检索 + 标签加权**的召回效果足够，
  而且零依赖、可离线、可解释（能说清为什么命中这条）。
  真要上向量检索，接口形状留在这里了 —— `search_knowledge` 换成
  向量召回即可，上层（API / Agent 工具）不用改。

检索打分（BM25-lite 简化版）：
  score = Σ(词频 × idf) + 标签命中加权 + 标题命中加权
  中文用「2-gram切分」近似分词（不引第三方分词器）。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeItem, MistakeLog

# 中文停用词（检索时剔除，否则「的/了/怎么」会污染打分）
STOPWORDS = {
    "的", "了", "和", "是", "在", "我", "有", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有",
    "看", "好", "自己", "这", "那", "什么", "怎么", "如何", "为", "与", "及",
    "the", "a", "an", "of", "to", "is", "in", "and", "or", "for",
}


# ── 分词 ────────────────────────────────────────────────────

def tokenize(text: str) -> list[str]:
    """中文 2-gram + 英文单词 的轻量切分。

    「知识库必须去重」→ ['知识', '识库', '库必', '必须', '须去', '去重']
    对短文本检索够用，且不需要 jieba 之类的依赖。
    """
    if not text:
        return []
    out: list[str] = []
    # 英文/数字词
    for w in re.findall(r"[A-Za-z0-9_]+", text):
        lw = w.lower()
        if lw not in STOPWORDS and len(lw) > 1:
            out.append(lw)
    # 中文 2-gram
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        if len(run) == 1:
            if run not in STOPWORDS:
                out.append(run)
            continue
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in STOPWORDS:
                out.append(bg)
    return out


# ── 检索 ────────────────────────────────────────────────────

def search_knowledge(
    db: Session,
    query: str,
    limit: int = 5,
    kind: str = "",
    tag: str = "",
) -> list[dict]:
    """检索知识库条目，返回按相关度排序的列表。"""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    stmt = select(KnowledgeItem)
    if kind:
        stmt = stmt.where(KnowledgeItem.kind == kind)
    rows = list(db.execute(stmt).scalars())
    if not rows:
        return []

    # 文档频率 → idf
    doc_tokens = [set(tokenize(
        f"{r.title} {r.why} {r.how} {r.pitfall} {r.tags}"
    )) for r in rows]
    n = len(rows)
    df = Counter()
    for toks in doc_tokens:
        df.update(toks)

    q_set = set(q_tokens)
    scored: list[tuple[float, KnowledgeItem]] = []
    for r, dtoks in zip(rows, doc_tokens):
        overlap = q_set & dtoks
        if not overlap:
            continue
        score = 0.0
        for t in overlap:
            idf = math.log(1 + n / (1 + df[t]))
            score += idf
        # 标题命中加权（标题是最强信号）
        if any(t in (r.title or "").lower() for t in q_set):
            score *= 1.8
        # 标签命中加权
        if tag and tag in (r.tags or ""):
            score *= 1.5
        # 高频条目略微加权（被验证过多次的经验更可靠）
        score *= 1 + min(r.hit_count, 10) * 0.02
        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return [
        {
            "id": r.id,
            "score": round(score, 3),
            "title": r.title,
            "kind": r.kind,
            "why": r.why,
            "how": r.how,
            "pitfall": r.pitfall,
            "tags": r.tags,
            "hit_count": r.hit_count,
        }
        for score, r in scored[:limit]
    ]


def search_mistakes(db: Session, query: str = "", limit: int = 5) -> list[dict]:
    """检索错误本。query 为空时返回最久未复盘的条目（供定期回顾）。"""
    if query:
        q = set(tokenize(query))
        rows = list(db.execute(select(MistakeLog).order_by(MistakeLog.created_at.desc())).scalars())
        out = []
        for r in rows:
            hay = f"{r.scene} {r.symptom} {r.cause} {r.fix}"
            if q & set(tokenize(hay)):
                out.append(_mistake_dict(r))
            if len(out) >= limit:
                break
        return out

    # 定期回顾：未复盘 + 超过 7 天的优先
    cutoff = datetime.now() - timedelta(days=7)
    rows = list(
        db.execute(
            select(MistakeLog)
            .where(MistakeLog.reviewed == 0)
            .order_by(MistakeLog.created_at.asc())
        ).scalars()
    )
    # 未复盘优先，同组内按时间先后
    rows.sort(key=lambda r: (r.created_at > cutoff, r.created_at))
    return [_mistake_dict(r) for r in rows[:limit]]


def _mistake_dict(r: MistakeLog) -> dict:
    return {
        "id": r.id,
        "scene": r.scene,
        "symptom": r.symptom,
        "cause": r.cause,
        "fix": r.fix,
        "reviewed": bool(r.reviewed),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def mark_reviewed(db: Session, mistake_id: int) -> dict:
    """标记错误已复盘。"""
    r = db.get(MistakeLog, mistake_id)
    if not r:
        return {"ok": False, "error": "记录不存在"}
    r.reviewed = 1
    r.reviewed_at = datetime.now()
    db.commit()
    return {"ok": True, "id": mistake_id}


def bump_hits(db: Session, ids: list[int]) -> None:
    """记录条目被检索到的次数（用于后续挑高频复看）。"""
    if not ids:
        return
    now = datetime.now()
    for i in ids:
        r = db.get(KnowledgeItem, i)
        if r:
            r.hit_count += 1
            r.last_hit_at = now
    db.commit()


# ── 沉淀 ────────────────────────────────────────────────────

def add_knowledge(
    db: Session,
    title: str,
    kind: str = "pitfall",
    why: str = "",
    how: str = "",
    pitfall: str = "",
    related: str = "",
    tags: str = "",
    source: str = "",
) -> dict:
    """新增/更新一条知识（同标题视为更新，避免重复沉淀）。"""
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "标题不能为空"}

    existing = db.execute(
        select(KnowledgeItem).where(KnowledgeItem.title == title)
    ).scalar_one_or_none()

    if existing:
        existing.kind = kind or existing.kind
        existing.why = why or existing.why
        existing.how = how or existing.how
        existing.pitfall = pitfall or existing.pitfall
        existing.related = related or existing.related
        existing.tags = tags or existing.tags
        existing.updated_at = datetime.now()
        db.commit()
        return {"ok": True, "id": existing.id, "action": "updated"}

    item = KnowledgeItem(
        title=title, kind=kind, why=why, how=how,
        pitfall=pitfall, related=related, tags=tags, source=source,
    )
    db.add(item)
    db.commit()
    return {"ok": True, "id": item.id, "action": "created"}


def add_mistake(
    db: Session,
    scene: str,
    symptom: str,
    cause: str = "",
    fix: str = "",
) -> dict:
    """记一条错误（错误本）。"""
    if not (symptom or "").strip():
        return {"ok": False, "error": "症状不能为空"}
    m = MistakeLog(scene=scene.strip(), symptom=symptom.strip(),
                   cause=cause.strip(), fix=fix.strip())
    db.add(m)
    db.commit()
    return {"ok": True, "id": m.id}


# ── Obsidian 导入 ──────────────────────────────────────────

_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _parse_ob_note(path) -> dict | None:
    """解析一篇 OB 笔记的 frontmatter + 四段正文。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    m = _FM.match(raw)
    tags = ""
    if m:
        fm = m.group(1)
        tm = re.search(r"tags:\s*\[(.*?)\]", fm)
        if tm:
            tags = tm.group(1).replace(" ", "").strip(",")
        raw = raw[m.end():]

    # 取一级标题作为 title（缺失则用文件名）
    hm = re.search(r"^#\s+(.+)$", raw, re.M)
    title = hm.group(1).strip() if hm else path.stem
    if not title or title.startswith("_"):
        return None  # 跳过说明类文件

    # 四段式切分
    sections: dict[str, str] = {}
    parts = re.split(r"^##\s+", raw, flags=re.M)
    head = parts[0]
    for p in parts[1:]:
        line = p.split("\n", 1)[0].strip()
        body = p.split("\n", 1)[1] if "\n" in p else ""
        if "为什么" in line:
            sections["why"] = body.strip()
        elif "怎么用" in line or "如何" in line:
            sections["how"] = body.strip()
        elif "反例" in line or "边界" in line:
            sections["pitfall"] = body.strip()
        elif "关联" in line:
            sections["related"] = body.strip()

    # 推断 kind
    low = title + head
    if "复盘" in low:
        kind = "review"
    elif "必须" in title or "不能" in title or "踩" in title:
        kind = "pitfall"
    elif "规范" in low or "规则" in low or "标准" in low:
        kind = "spec"
    else:
        kind = "insight"

    return {
        "title": title,
        "kind": kind,
        "why": sections.get("why", "")[:1500],
        "how": sections.get("how", "")[:1500],
        "pitfall": sections.get("pitfall", "")[:1000],
        "related": sections.get("related", "")[:500],
        "tags": tags,
        "source": str(path),
    }


def import_obsidian(db: Session, root: str, subdir: str = "30-知识库") -> dict:
    """从 Obsidian 库导入知识条目。

    幂等：按标题 upsert，重复导入不会产生重复条目。
    """
    from pathlib import Path

    base = Path(root) / subdir
    if not base.exists():
        return {"ok": False, "error": f"目录不存在：{base}", "imported": 0}

    created = updated = skipped = 0
    for p in sorted(base.glob("*.md")):
        info = _parse_ob_note(p)
        if not info:
            skipped += 1
            continue
        r = add_knowledge(db=db, **info)
        if not r.get("ok"):
            skipped += 1
        elif r.get("action") == "created":
            created += 1
        else:
            updated += 1

    total = db.execute(select(func.count(KnowledgeItem.id))).scalar_one()
    return {
        "ok": True, "root": str(base),
        "created": created, "updated": updated, "skipped": skipped,
        "total": total,
    }


def knowledge_stats(db: Session) -> dict:
    """知识库概览（供前端看板）。"""
    total = db.execute(select(func.count(KnowledgeItem.id))).scalar_one()
    by_kind = dict(
        db.execute(
            select(KnowledgeItem.kind, func.count(KnowledgeItem.id))
            .group_by(KnowledgeItem.kind)
        ).all()
    )
    mistakes = db.execute(select(func.count(MistakeLog.id))).scalar_one()
    pending = db.execute(
        select(func.count(MistakeLog.id)).where(MistakeLog.reviewed == 0)
    ).scalar_one()
    top = list(
        db.execute(
            select(KnowledgeItem).order_by(
                KnowledgeItem.hit_count.desc(), KnowledgeItem.id.desc()
            ).limit(5)
        ).scalars()
    )
    return {
        "total": total,
        "by_kind": by_kind,
        "mistakes": mistakes,
        "pending_review": pending,
        "top_hit": [{"id": t.id, "title": t.title, "hits": t.hit_count} for t in top],
    }
