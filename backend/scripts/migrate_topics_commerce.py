"""为 topics 表补商业维度列（SQLite ALTER TABLE）。

`Base.metadata.create_all()` 只建新表，**不给已存在的表加列**。
所以老库必须显式 ALTER，否则报 `no such column: topics.persona_id`。

幂等：列已存在就跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.db.session import engine  # noqa: E402

# (表名, 列名, DDL 类型)
NEW_COLUMNS = [
    ("topics", "persona_id", "INTEGER"),
    ("topics", "product_id", "INTEGER"),
    ("topics", "scene", "VARCHAR(50) DEFAULT ''"),
    ("topics", "pain_point", "VARCHAR(300) DEFAULT ''"),
    ("topics", "intent_stage", "VARCHAR(20) DEFAULT ''"),
    ("topics", "commercial_intent", "FLOAT DEFAULT 0.0"),
    ("topics", "evidence", "TEXT"),
    ("topics", "search_intent", "VARCHAR(300) DEFAULT ''"),
]

INDEXES = [
    ("ix_topics_product_id", "topics", "product_id"),
    ("ix_topics_scene", "topics", "scene"),
    ("ix_topics_intent_stage", "topics", "intent_stage"),
    ("ix_topics_commercial_intent", "topics", "commercial_intent"),
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

        # evidence 是 JSON 列，SQLite 用 TEXT 存；补上默认值
        if "topics" in tables:
            conn.execute(text(
                "UPDATE topics SET evidence = '[]' WHERE evidence IS NULL"
            ))

        for idx_name, table, col in INDEXES:
            if table in tables:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col})"
                ))
        print("  ✓ 索引已就绪")

    print(f"迁移完成，新增 {added} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
