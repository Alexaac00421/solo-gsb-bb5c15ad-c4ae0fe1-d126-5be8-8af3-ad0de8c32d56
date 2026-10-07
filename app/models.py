from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, UTCDateTime, utcnow


class EventType(str, enum.Enum):
    temperature = "temperature"
    handover = "handover"


class Verdict(str, enum.Enum):
    qualified = "qualified"          # 合格
    unqualified = "unqualified"      # 不合格
    evidence_pending = "evidence_pending"  # 待补证


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    box_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    min_temp: Mapped[float] = mapped_column(Float, nullable=False)
    max_temp: Mapped[float] = mapped_column(Float, nullable=False)
    max_sample_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    first_custodian: Mapped[str] = mapped_column(String(128), nullable=False)

    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # 当前结论（关闭前恒为 NULL，表示关闭前不得给最终结论）
    current_verdict: Mapped[Verdict | None] = mapped_column(
        Enum(Verdict, name="verdict"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    events: Mapped[list["Event"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )
    conclusions: Mapped[list["ConclusionRecord"]] = relationship(
        back_populates="transfer",
        cascade="all, delete-orphan",
        order_by="ConclusionRecord.version",
    )

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_transfer_time_order"),
        CheckConstraint("max_temp >= min_temp", name="ck_transfer_temp_order"),
        CheckConstraint(
            "max_sample_interval_seconds > 0", name="ck_transfer_interval_positive"
        ),
    )


class Event(Base):
    """离线补传的原始事件：温度读数或交接记录。原始事件不得删除/篡改。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer_id: Mapped[int] = mapped_column(
        ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False
    )
    event_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type"), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow
    )

    # 温度读数字段
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 交接字段
    giver: Mapped[str | None] = mapped_column(String(128), nullable=True)
    receiver: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 幂等去重：内容规范化后的哈希，同 ID 同内容重传视为重复
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    transfer: Mapped[Transfer] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint(
            "transfer_id", "event_uid", name="uq_event_transfer_uid"
        ),
        Index("ix_events_transfer_collected", "transfer_id", "collected_at"),
    )


class ConclusionRecord(Base):
    """每次关闭后核验产生的结论版本；关闭后补传会追加新版本。"""

    __tablename__ = "conclusion_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer_id: Mapped[int] = mapped_column(
        ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[Verdict] = mapped_column(Enum(Verdict, name="verdict"), nullable=False)
    reasons: Mapped[list["ConclusionReason"]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )
    # 本次结论所依据的事件（按采集时间排序的 event 主键列表）与统计
    evidence_event_ids: Mapped[list[int]] = mapped_column(
        JSON, nullable=False
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    temperature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    handover_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)

    transfer: Mapped[Transfer] = relationship(back_populates="conclusions")

    __table_args__ = (
        UniqueConstraint("transfer_id", "version", name="uq_conclusion_version"),
    )


class ConclusionReason(Base):
    __tablename__ = "conclusion_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("conclusion_records.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    event_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    record: Mapped[ConclusionRecord] = relationship(back_populates="reasons")
