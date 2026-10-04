"""数据库会话管理。

MVP 用 SQLite 零依赖；生产改PostgreSQL 只需改 settings.database_url。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base


def _build_engine():
    """按数据库类型构造引擎。

    SQLite 需要 check_same_thread=False 才能在 FastAPI 多线程下使用。
    PostgreSQL 走连接池（生产建议加 pool_size / max_overflow）。
    """
    if settings.database_url.startswith("sqlite"):
        return create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            echo=False,
            future=True,
        )
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
