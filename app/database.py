from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://sample:sample@db:5432/sample_transfer",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """统一的 UTC 时间类型。

    应用层始终使用时区感知时间；落库时转换为 UTC 朴素时间
    （PostgreSQL 上为 TIMESTAMP WITHOUT TIME ZONE，语义按 UTC 解释），
    读取时再补回 UTC 时区。这样在 PostgreSQL 与 SQLite
    （用于本地无 Postgres 的测试）上行为完全一致。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
