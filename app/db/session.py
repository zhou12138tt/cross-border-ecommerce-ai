"""数据库会话工厂。

Demo 默认走 SQLite 同步引擎（简单可靠）；切到 PostgreSQL 时可改 create_async_engine。
"""
from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=settings.APP_DEBUG and settings.is_sqlite,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def init_db() -> None:
    """创建所有表（首次启动 / 演示用）。"""
    # 触发模型注册
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：获取 DB 会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
