"""清理历史脏数据 + 补齐商品/人群基础数据（2026-10）。

处理三类问题：
  1. **状态脏数据** —— 选题 done但其稿件校验不通过（topic 13 / draft 4）
  2. **垃圾关键词** —— 「月广州海关监」「天高速公路新」这类滑窗碎片
  3. **基础数据缺失** —— 商品库与人群卡是空的，选题无从归属

★ 原则：**不删数据，只标记**。
  垃圾选题改成 archived 而不是 delete——保留历史才能看出模式。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.models import (  # noqa: E402
    Draft,
    Persona,
    Product,
    Topic,
    TopicStatus,
)
from app.db.session import SessionLocal  # noqa: E402
from app.services.topic_state import sync_topic_status  # noqa: E402

# 滑窗碎片特征（与 topic_converter 的教训一致）：
# 正则定长切出来的碎片，通常包含「数字夹在中间」或以虚词碎片结尾
_SLIDING_WINDOW = re.compile(r"[0-9０-９]")


def looks_like_garbage(kw: str) -> bool:
    """判断关键词是否是机械截断的碎片。

    真实长尾词不该含孤立数字（「充电桩排队」好，「天高速公路新」坏）。
    """
    if not kw:
        return False
    # 数字出现在词中间通常是标题里的「第N天/3小时」被切进来了
    if _SLIDING_WINDOW.search(kw) and not re.match(r"^[0-9]+[元块件个]", kw):
        return True
    # 以常见虚词碎片结尾
    if re.search(r"(的|了|在|和|与|为|从|到)$", kw):
        return True
    return False


def main() -> int:
    db = SessionLocal()
    try:
        report: dict[str, int] = {}

        # ── 1. 修状态脏数据 ──
        fixed = 0
        for d in db.execute(select(Draft)).scalars():
            topic = getattr(d, "topic", None)
            if topic is None:
                continue
            passed = bool((d.validation or {}).get("passed"))
            if not passed and topic.status == TopicStatus.DONE:
                before = topic.status
                sync_topic_status(db, d)
                if topic.status != before:
                    fixed += 1
                    print(f"  ✓ 选题 #{topic.id} {before} → {topic.status}"
                          f"（稿件 #{d.id} 校验未通过）")
        db.commit()
        report["status_fixed"] = fixed

        # ── 2. 标记垃圾关键词选题 ──
        archived = 0
        for t in db.execute(select(Topic)).scalars():
            if looks_like_garbage(t.keyword_target or "") and t.status != TopicStatus.ARCHIVED:
                t.status = TopicStatus.ARCHIVED
                t.keyword_target = ""  # 清空垃圾词，标注待人工重填
                archived += 1
        db.commit()
        report["garbage_archived"] = archived

        # ── 3. 标记校验不通过的稿件 ──
        bad = 0
        for d in db.execute(select(Draft)).scalars():
            if not (d.validation or {}).get("passed"):
                bad += 1
        report["bad_drafts"] = bad

        # ── 4. 补基础数据（幂等）──
        if db.execute(select(Product)).first() is None:
            db.add(Product(
                name="一次性内裤（工厂直供）",
                sku="UW-001",
                category="underwear",
                price_band="9.9-19.9/条",
                unit="条",
                selling_points=[
                    "无纺布一体成型，无缝无标，不勒不磨",
                    "独立铝箔包装，开封即用",
                    "100 条起订，7 天打样",
                    "现货 3 万条，48 小时发",
                ],
                pain_points=[
                    "出差旅行时不想用酒店公用贴身用品",
                    "经期担心闷、怕侧漏",
                    "大码担心勒、卷边、透",
                    "夏季久坐担心闷出汗",
                ],
                scenes=["差旅", "经期", "夏季", "通勤", "旅行"],
                # ★ certs 是「抗菌」等宣称能否合法说的依据。
                #   没有检测报告就不要往里加——加了校验器就会放行，风险自负。
                certs=[],
                proof_assets=[],
                moq="100 条起订",
                notes="★待补充：车间/产线/质检工序/包装/面料微距实拍素材"
                     "（AI 生图做不出真车间，实拍才是差异化地基）",
            ))
            db.commit()
            report["products_seeded"] = 1
        else:
            report["products_seeded"] = 0

        if db.execute(select(Persona)).first() is None:
            seed_personas = [
                Persona(
                    name="差旅职场女性", age_range="25-35", life_stage="职场",
                    concerns=["酒店公用贴身用品", "行李空间有限", "闷"],
                    objections=["太贵", "一次性穿一次就扔是不是浪费"],
                    own_words=[],
                ),
                Persona(
                    name="经期女性", age_range="18-40", life_stage="生理期",
                    concerns=["闷", "怕侧漏", "会不会过敏"],
                    objections=["担心不安全", "怕有味道"],
                    own_words=[],
                ),
                Persona(
                    name="大码女性", age_range="25-45", life_stage="日常",
                    concerns=["勒", "卷边", "透", "起球"],
                    objections=["尺码不对", "大码贵"],
                    own_words=[],
                ),
            ]
            for p in seed_personas:
                db.add(p)
            db.commit()
            report["personas_seeded"] = len(seed_personas)
        else:
            report["personas_seeded"] = 0

        print()
        print("=== 清理完成 ===")
        for k, v in report.items():
            print(f"  {k}: {v}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
