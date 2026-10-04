"""数据库会话管理。

MVP 用 SQLite 零依赖；生产改PostgreSQL 只需改 settings.database_url。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base


def _build_engine():
    """按数据库类型构造引擎。

    SQLite 需要 check_same_thread=False 才能在 FastAPI 多线程下使用。
    PostgreSQL 走连接池（生产建议加 pool_size / max_overflow）。

    ★ SQLite 并发修复（2026-10）：
      原来没开 WAL，`journal_mode=delete`下一个写事务会持有文件锁，
      而本项目写操作很频繁（每次 RSS 采集 commit、每次知识库检索
      bump_hits写、每次 AI 调用开session 读写）。
      并发下典型报错是 `sqlite3.OperationalError: database is locked`。
      WAL 是单文件 SQLite 支持并发的**唯一有效手段**。
    """
    if settings.database_url.startswith("sqlite"):
        eng = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False, "timeout": 15},
            echo=False,
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            """每个连接设一次 PRAGMA。"""
            cur = dbapi_conn.cursor()
            try:
                # WAL：读写不互相阻塞
                cur.execute("PRAGMA journal_mode=WAL")
                # 等锁而不是立刻报 database is locked
                cur.execute("PRAGMA busy_timeout=15000")
                # NORMAL 在 WAL 下是安全且快的（官方推荐组合）
                cur.execute("PRAGMA synchronous=NORMAL")
                # ★ 开启外键约束 —— SQLite 默认**不开启**，
                #   导致 models 里的 ForeignKey(...ondelete="SET NULL")
                #   形同虚设，会静默产生孤儿稿件。
                cur.execute("PRAGMA foreign_keys=ON")
            finally:
                cur.close()

        return eng

    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表。MVP 直接 create_all，正式项目应改用 Alembic 迁移。"""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用的会话工厂。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
