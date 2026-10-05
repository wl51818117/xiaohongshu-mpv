"""为 raw_materials / topics 补两个新字段（SQLite ALTER TABLE）。

新增：
  raw_materials.cover_url  —— 浏览器采集时转存到本机的封面图
  topics.originality_risk  —— 从素材继承的原创风险

`Base.metadata.create_all()` 只建新表，**不给已存在的表加列**。
幂等：列已存在就跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.db.session import engine  # noqa: E402

NEW_COLUMNS = [
    ("raw_materials", "cover_url", "VARCHAR(500) DEFAULT ''"),
    ("topics", "originality_risk", "VARCHAR(20) DEFAULT 'low'"),
]


def main() -> int:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    added = 0

    with engine.begin() as conn:
        for table, col, ddl in NEW_COLUMNS:
            if table not in tables:
                print(f"  跳过 {table}（表不存在）")
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if col in existing:
                print(f"  · {table}.{col} 已存在")
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            print(f"  ✓ {table}.{col}")
            added += 1

    print(f"迁移完成，新增 {added} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
