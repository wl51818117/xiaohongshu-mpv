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
    now = datetime.now()

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

        # ── 维度二：投票分（借鉴 ExpeL 的 rule count）──
        # 原来只按 hit_count 加权，最大 +20%，区分度太弱，烂经验不会沉底。
        # 换成投票分后，被反复验证的经验权重可达 2 倍，被质疑的自动沉底。
        if r.status == "retired":
            continue
        score *= 1.0 + min(r.score, 10) * 0.1

        # ── 维度三：新近性（借鉴 Generative Agents 的 recency 衰减）──
        # 经验越新越可信，但**权重封顶 30%**——
        # 否则老经验会被系统性遗忘，而那些往往正是踩过的坑。
        if r.created_at:
            age_days = max(0, (now - r.created_at).days)
            recency = 0.9 ** min(age_days, 60)
            score *= 0.7 + 0.3 * recency

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


# ── 经验投票分（借鉴 ExpeL LeapLabTHU/ExpeL）────────────────

# 计分规则，对齐 ExpeL 的 update_rules()：
#   ADD   +2  新经验给2 分，快速建立
#   AGREE +1  被再次验证
#   EDIT  +1  改写同时保留分数
#   REMOVE -1被质疑；列表满时 -3，加速淘汰
ACTION_DELTA = {"add": 2, "agree": 1, "edit": 1, "remove": -1, "challenge": -1}
# 列表已满时质疑的加重扣分（模拟「位置稀缺时更严格淘汰」）
FULL_PENALTY = -3


def apply_vote(
    db: Session,
    title: str,
    action: str,
    why: str = "",
    how: str = "",
    pitfall: str = "",
    tags: str = "",
) -> dict:
    """对一条经验投票（新增/复用/改写/质疑），自动维护投票分与退休。

    ★ 为什么需要投票分：
      知识库只增不减会越存越乱，烂经验和好经验混在一起无法区分。
      投票分让经验「沉淀 + 淘汰」自动循环：
        被反复验证 → 分数升 → 检索时排前面
        长期没人用/被质疑 → 分数降 → 自动退休（不删除，保留历史）
    """
    action = (action or "add").lower().strip()
    if action not in ACTION_DELTA:
        action = "add"

    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "标题不能为空"}

    item = db.execute(
        select(KnowledgeItem).where(KnowledgeItem.title == title)
    ).scalar_one_or_none()

    # 活跃条目太多时，质疑扣加重分（让淘汰更快）
    active = db.execute(
        select(func.count(KnowledgeItem.id)).where(KnowledgeItem.status == "active")
    ).scalar_one()
    delta = ACTION_DELTA[action]
    if action in ("remove", "challenge") and active > 100:
        delta = FULL_PENALTY

    if item is None:
        # 质疑一条不存在的经验 = 新增（避免模型幻觉出不存在条目被删）
        if action in ("remove", "challenge"):
            item = KnowledgeItem(
                title=title, kind="insight", why=why, how=how,
                pitfall=pitfall, tags=tags, score=0, status="retired",
            )
            db.add(item)
            db.commit()
            return {"ok": True, "id": item.id, "action": "created",
                    "score": 0, "status": "retired",
                    "note": "质疑了一条不存在的经验，已记为退休"}

        item = KnowledgeItem(
            title=title, kind="insight", why=why, how=how,
            pitfall=pitfall, tags=tags, score=delta, status="active",
        )
        db.add(item)
        db.commit()
        return {"ok": True, "id": item.id, "action": "created",
                "score": item.score, "status": item.status}

    # 已存在：改写内容并调整分数
    if action in ("agree", "edit"):
        item.why = why or item.why
        item.how = how or item.how
        item.pitfall = pitfall or item.pitfall
        item.tags = tags or item.tags
    elif action == "add":
        # 同标题再次新增 = 视为「再次验证」
        action = "agree"

    item.score = max(0, item.score + delta)
    # 分数归零 → 退休（不物理删除，历史仍有价值）
    if item.score <= 0:
        item.status = "retired"
    elif item.status == "retired" and action != "challenge":
        # 被重新验证则复活
        item.status = "active"
    item.updated_at = datetime.now()
    db.commit()
    return {
        "ok": True, "id": item.id, "action": action,
        "score": item.score, "status": item.status,
    }


def promote_mistake(db: Session, mistake_id: int) -> dict:
    """复盘结论晋升为正式经验（借鉴 ADR 的 rejected 永不删除思路）。

    流程：MistakeLog（错误本）→ 复盘 → KnowledgeItem（知识库）
    复盘结论沉淀成经验，错误本标 reviewed，历史可追溯。
    """
    m = db.get(MistakeLog, mistake_id)
    if not m:
        return {"ok": False, "error": "记录不存在"}

    # 用「症状」作标题，根因作 why，解法作 how
    title = (m.symptom or "").strip()[:200]
    if not title:
        return {"ok": False, "error": "症状为空，无法晋升"}

    r = add_knowledge(
        db=db,
        title=title,
        kind="pitfall",
        why=m.cause or "",
        how=m.fix or "",
        pitfall=f"（错误本 #{m.id} · {m.scene}）",
        tags=f"错误本,{(m.scene or '未分类')}",
        source=f"mistake:{m.id}",
    )
    if not r.get("ok"):
        return r

    m.reviewed = 1
    m.reviewed_at = datetime.now()
    db.commit()
    return {"ok": True, "knowledge_id": r.get("id"), "mistake_id": mistake_id}


def inject_experience(db: Session, query: str, limit: int = 3) -> str:
    """检索历史经验并拼成可注入提示词的文本。

    ★ 这是「持续增强」的接线点：
      经验不只是给人看的，还要喂给模型。
      借鉴 Reflexion 的做法——把反思写进记忆，下次任务带着它。
    """
    if not query or not query.strip():
        return ""
    try:
        hits = search_knowledge(db, query=query, limit=limit)
    except Exception:  # noqa: BLE001
        return ""
    if not hits:
        return ""
    # 只注入可执行的部分（how），不注入 why —— 模型需要的是「怎么做」
    lines = []
    for h in hits:
        how = (h.get("how") or "").strip()
        title = (h.get("title") or "").strip()
        if how:
            lines.append(f"- {title}：{how[:180]}")
        elif title:
            lines.append(f"- {title}")
    if not lines:
        return ""
    bump_hits(db, [h["id"] for h in hits])
    return (
        "【历史经验（本工作台已验证的结论，优先遵循）】\n"
        + "\n".join(lines)
        + "\n\n"
    )


# ── Obsidian 导入 ──────────────────────────────────────────

_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
# 表格行：| 现象 | 真因 | 解法 |
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


def _clean_cell(s: str) -> str:
    """清掉 markdown 表格单元格的记号与转义。"""
    s = s.replace("**", "").replace("`", "").strip()
    return re.sub(r"\\([|*`])", r"\1", s).strip()


def _parse_pitfall_table(raw: str, path, tags: str) -> list[dict]:
    """解析「踩坑账本」式的表格：| 现象 | 真因 | 解法 |。

    ★ OB 里每个项目的 40-记录/踩坑账本.md 都是这个格式，
      一条坑就是一行，价值很高但原解析器只认四段式，会整篇漏掉。

    返回多条知识条目（每行一条），而不是一篇。
    """
    items: list[dict] = []
    headers: list[str] = []
    section = ""

    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("##"):
            section = s.lstrip("#").strip()
            continue
        m = _TABLE_ROW.match(s)
        if not m:
            # 分隔行|---|---| 跳过
            if headers and re.fullmatch(r"[\s|:\-]+", s):
                continue
            headers = []
            continue

        cells = [_clean_cell(c) for c in m.group(1).split("|")]

        # 首个表格当表头
        if not headers:
            if any(h in cells for h in ("现象", "问题", "症状")):
                headers = cells
                continue
            headers = ["现象", "真因", "解法"]  # 无表头时按位置猜

        if len(cells) < 2:
            continue
        # 全空的行跳过
        if not any(cells):
            continue

        phenomenon = cells[0] if cells else ""
        cause = cells[1] if len(cells) > 1 else ""
        # 解法可能在第 3 列，也可能只有 2 列（现象+解法）
        fix = cells[2] if len(cells) > 2 else ""
        if not phenomenon or phenomenon in ("现象", "问题", "症状"):
            continue

        # 表头是「现象|真因|解法」时，cause/fix 分别是真因与解法
        if len(headers) >= 3 and ("真因" in headers[1] or "原因" in headers[1]):
            title = phenomenon[:200]
            why = cause
            how = fix
        else:
            # 两列：现象 + 解决方案
            title = phenomenon[:200]
            why = ""
            how = cause

        if not title:
            continue

        items.append({
            "title": title,
            "kind": "pitfall",
            # 表格里「真因」列就是 why，「解法」列就是 how
            "why": why[:1500],
            "how": how[:1500],
            "pitfall": f"（来源：{section}）" if section else "",
            "related": "",
            "tags": ",".join(t for t in (tags, "踩坑账本", section.replace(" ", "")) if t),
            "source": str(path),
        })
    return items


def _parse_ob_note(path) -> dict | None:
    """解析一篇 OB 笔记：优先按四段式；命中表格则按行拆多条。

    返回 dict（单条）或 list[dict]（表格多行）或 None（跳过）。
    """
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

    # ★ 表格优先：踩坑账本一篇能出几十条，比当一篇读有价值得多
    if re.search(r"^\s*\|.*(现象|问题|症状).*\|", raw, re.M):
        rows = _parse_pitfall_table(raw, path, tags)
        if rows:
            return rows  # type: ignore[return-value]

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

    # 没有四段式但有正文：整篇当 how（总比丢掉强）
    if not any(sections.values()):
        body = re.sub(r"^#\s+.+$", "", head, flags=re.M).strip()
        if len(body) > 20:
            sections["how"] = body
        else:
            return None

    # 推断 kind
    low = title + head
    if "踩坑" in low or "账本" in low:
        kind = "pitfall"
    elif "复盘" in low or "总结" in low:
        kind = "review"
    elif "规范" in low or "规则" in low or "标准" in low or "协议" in low:
        kind = "spec"
    elif "必须" in title or "不能" in title or "不要" in title:
        kind = "pitfall"
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


# OB 库的目录约定（docs: _项目标准结构.md）
OB_DIRS = {
    "30-知识库": "知识库",
    "40-记录": "踩坑账本",
    "10-项目": "项目文档",
    "20-方案": "方案决策",
    "00-总览": "项目总览",
    "思维复盘": "思维复盘",
    "06-记忆中枢": "记忆中枢",
    "股票学习": "学习笔记",
    "00-Inbox": "收件箱",
    "02-项目": "项目归档",
    "20-领域": "领域知识",
    "90-归档": "归档",
}


def import_obsidian(db: Session, root: str, subdir: str = "30-知识库") -> dict:
    """从 Obsidian 库导入知识条目。

    ★subdir 传 "" 表示**递归导入全库**（105 个 md），
      踩坑账本/项目文档/复盘/方案决策都会进来。

    幂等：按标题 upsert，重复导入不产生重复条目。
    """
    from pathlib import Path

    base = Path(root) / subdir if subdir else Path(root)
    if not base.exists():
        return {"ok": False, "error": f"目录不存在：{base}", "imported": 0}

    # 递归 or 单层
    files = sorted(base.rglob("*.md") if not subdir else base.glob("*.md"))

    created = updated = skipped = files_n = 0
    kinds: dict[str, int] = {}

    for p in files:
        files_n += 1
        parsed = _parse_ob_note(p)
        if not parsed:
            skipped += 1
            continue
        # 表格解析会返回 list
        entries = parsed if isinstance(parsed, list) else [parsed]
        for info in entries:
            r = add_knowledge(db=db, **info)
            if not r.get("ok"):
                skipped += 1
            else:
                kinds[info["kind"]] = kinds.get(info["kind"], 0) + 1
                if r.get("action") == "created":
                    created += 1
                else:
                    updated += 1

    total = db.execute(
        select(func.count(KnowledgeItem.id)).where(KnowledgeItem.status == "active")
    ).scalar_one()
    return {
        "ok": True, "root": str(base),
        "files_scanned": files_n,
        "created": created, "updated": updated, "skipped": skipped,
        "kinds": kinds,
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
