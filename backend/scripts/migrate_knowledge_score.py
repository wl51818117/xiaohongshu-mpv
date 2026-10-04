"""轻量迁移：为KnowledgeItem 补score / status 字段。

用SQLite 的 ALTER TABLE，不引Alembic —— 单机单库场景下
手写迁移比引入框架更省事，且能看清每一步在做什么。

幂等：字段已存在就跳过。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import engine  # noqa: E402


def main() -> int:
    insp = inspect(engine)
    table = "knowledge_items"
    if table not in insp.get_table_names():
        print(f"表 {table} 不存在，先跑 init_db")
        return 1

    existing = {c["name"] for c in insp.get_columns(table)}
    changes = [
        ("score", "INTEGER NOT NULL DEFAULT 2"),
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
    ]

    added = 0
    with engine.begin() as conn:
        for name, ddl in changes:
            if name in existing:
                print(f"  {name} 已存在，跳过")
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            print(f"  ✓ 新增 {name}")
            added += 1

        # 补索引（SQLite 支持 IF NOT EXISTS）
        for idx in ("ix_knowledge_items_score", "ix_knowledge_items_status"):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx} ON {table} "
                              f"({idx.replace('ix_knowledge_items_', '')})"))
        print("  ✓ 索引已就绪")

        # 老数据统一给默认分
        conn.execute(text(
            f"UPDATE {table} SET score = 2 WHERE score IS NULL OR score = 0"
        ))

    print(f"迁移完成，新增 {added} 个字段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
