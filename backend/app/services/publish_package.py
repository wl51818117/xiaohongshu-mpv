"""发布包组装与发布前校验（M5）。

把一篇稿件 + 素材组装成可直接投递的「发布包」，并跑完整的发布前检查。

★ 设计原则：
  - 校验必须**确定性**，不能靠 LLM —— 平台红线不能靠猜
  - 任何一项不通过都不产出可发布状态，但**仍返回完整报告**（让人知道还差什么）
  - 这里只做「组装 + 自检」，**不做平台提交**（那是M6，且必须人工点）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Draft
from app.services import draft_validator
from app.services.asset_generator import validate_asset
from app.services.topic_converter import COMPLIANCE_BLOCKLIST  # noqa: E402

# 平台硬规格（docs/02）
TITLE_MAX = 20
BODY_MIN, BODY_MAX = 300, 800
IMG_MIN, IMG_MAX = 4, 8
TAGS_MIN, TAGS_MAX = 3, 5

AI_DECLARATION_HINT = "本内容含 AI 辅助生成部分"


@dataclass
class CheckItem:
    """单项检查结果。"""

    key: str
    label: str
    passed: bool
    detail: str = ""
    blocking: bool = True


def build_publish_package(db: Session, draft_id: int) -> dict[str, Any]:
    """组装发布包并跑发布前全量检查。"""
    draft = db.get(Draft, draft_id)
    if not draft:
        return {"ok": False, "error": "稿件不存在"}

    topic = draft.topic
    keyword = (topic.keyword_target if topic else "") or ""
    images = list(draft.images or [])
    covers = [p for p in images if "cover_" in p]
    inners = [p for p in images if "inner_" in p]

    # ── 组装 ──
    package = {
        "draft_id": draft.id,
        "pipeline_type": draft.pipeline_type,
        "title": draft.title,
        "body": draft.body,
        "tags": list(draft.tags or []),
        "cover": draft.cover_url,
        "cover_candidates": covers,
        "images": inners,
        "keyword": keyword,
        "topic_title": (topic.title if topic else ""),
        "ai_declaration": draft.ai_declaration,
        # 发布时必须手动确认的项，我们只给建议值
        "suggested": {
            "content_type_declaration": "含 AI 合成内容（发布页必勾）",
            "visibility": "公开",
            "poi": "本地探店类必带；否则不带",
            "product_link": "带货笔记需关联商品卡片",
        },
    }

    checks = _run_checks(draft, package)
    blockers = [c for c in checks if c.blocking and not c.passed]

    return {
        "ok": not blockers,
        "package": package,
        "checks": [
            {
                "key": c.key,
                "label": c.label,
                "passed": c.passed,
                "detail": c.detail,
                "blocking": c.blocking,
            }
            for c in checks
        ],
        "passed_count": sum(1 for c in checks if c.passed),
        "total": len(checks),
        "blocked_count": len(blockers),
        "note": (
            "本服务只做组装与自检，**不代提交平台** —— "
            "发布必须人工点击（平台 2026 封禁 AI 托管账号）"
        ),
    }


def _run_checks(draft: Draft, pkg: dict[str, Any]) -> list[CheckItem]:
    """发布前 12 项检查。"""
    items: list[CheckItem] = []
    title = pkg["title"] or ""
    body = pkg["body"] or ""
    tags = pkg["tags"]
    images = pkg["images"]
    covers = pkg["cover_candidates"]
    keyword = pkg["keyword"]

    # 1标题长度
    items.append(
        CheckItem(
            "title_len",
            "标题长度 ≤20 字",
            0 < len(title) <= TITLE_MAX,
            f"当前 {len(title)} 字",
        )
    )

    # 2 标题埋词
    if keyword:
        core = _core_terms(keyword)
        hit = any(t in title for t in core)
        items.append(
            CheckItem("title_keyword", "标题含长尾词核心词", hit,
                      f"核心词 {'/'.join(core)}")
        )

    # 3 正文长度
    items.append(
        CheckItem(
            "body_len",
            f"正文 {BODY_MIN}-{BODY_MAX} 字",
            BODY_MIN <= len(body) <= BODY_MAX,
            f"当前 {len(body)} 字",
        )
    )

    # 4 正文埋词
    if keyword:
        core = _core_terms(keyword)
        pos = min((body.find(t) for t in core if t in body), default=-1)
        ok = pos != -1 and pos <= 80
        items.append(
            CheckItem("body_keyword", "正文前 80 字含长尾词", ok,
                      f"位置第 {pos + 1} 字" if pos >= 0 else "未出现")
        )

    # 5 核心词不堆砌
    if keyword:
        core = _core_terms(keyword)
        n = max((body.count(t) for t in core), default=0)
        items.append(
            CheckItem("keyword_freq", "核心词 2-3 次（不堆砌）", n <= 3, f"当前 {n} 次")
        )

    # 6 标签数量
    items.append(
        CheckItem("tags_count", f"标签 {TAGS_MIN}-{TAGS_MAX} 个",
                  TAGS_MIN <= len(tags) <= TAGS_MAX, f"当前 {len(tags)} 个")
    )

    # 7 封面存在
    items.append(
        CheckItem("cover", "封面已设置", bool(pkg["cover"]),
                  pkg["cover"] or "未设置")
    )

    # 8 封面比例
    if pkg["cover"]:
        v = validate_asset(pkg["cover"], "image")
        items.append(
            CheckItem("cover_ratio", "封面 3:4（1080×1440）", v["passed"],
                      "; ".join(v.get("issues", []))[:50])
        )
    else:
        items.append(CheckItem("cover_ratio", "封面 3:4（1080×1440）", False, "无封面"))

    # 9 内页数量
    if draft.pipeline_type == "image":
        items.append(
            CheckItem("images_count", f"内页 {IMG_MIN}-{IMG_MAX} 张",
                      IMG_MIN <= len(images) <= IMG_MAX, f"当前 {len(images)} 张")
        )
    else:
        items.append(
            CheckItem("video_asset", "视频素材已生成", bool(images),
                      f"{len(images)} 个文件")
        )

    # 10 素材可访问
    broken = []
    for p in ([pkg["cover"]] if pkg["cover"] else []) + images:
        from app.services.asset_generator import ASSET_ROOT
        real = (ASSET_ROOT.parent / p)
        if not real.exists():
            broken.append(p.split("/")[-1])
    items.append(
        CheckItem("assets_exist", "素材文件均存在", not broken,
                  f"缺失 {broken[:2]}" if broken else "全部就位")
    )

    # 11 合规黑名单
    # 注意：topic_converter.COMPLIANCE_BLOCKLIST 是**扁平 list**（不是 dict），
    #      早期误按 dict 用 .items() 导致 500 —— 这里直接遍历。
    full = f"{title}\n{body}\n{' '.join(tags)}".lower()
    hits = [w for w in COMPLIANCE_BLOCKLIST if w and w.lower() in full]
    items.append(
        CheckItem(
            "compliance",
            "无站外导流/极限词/违规表述",
            not hits,
            f"命中 {'、'.join(hits[:3])}" if hits else "未命中",
        )
    )

    # 12 AI 声明（平台强制）
    items.append(
        CheckItem("ai_declaration", "AI 内容声明已填",
                  bool((draft.ai_declaration or "").strip()),
                  draft.ai_declaration or "未填写")
    )

    return items


def _core_terms(keyword: str) -> list[str]:
    """长尾词取核心词（与校验器一致）。"""
    import re
    terms = [t for t in re.split(r"[\s、,，/|+]+", keyword) if t]
    terms.sort(key=len, reverse=True)
    return terms[:2] or [keyword]
