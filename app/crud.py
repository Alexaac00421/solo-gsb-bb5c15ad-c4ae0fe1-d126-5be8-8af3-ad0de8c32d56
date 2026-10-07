from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import utcnow
from .models import (
    ConclusionReason,
    ConclusionRecord,
    Event,
    EventType,
    Transfer,
    Verdict,
)
from .schemas import (
    ConclusionOut,
    EventAccepted,
    EventOut,
    HandoverEventCreate,
    ReasonOut,
    TemperatureEventCreate,
    TransferCreate,
    TransferOut,
)
from .verification import verify


def _content_hash(
    event_type: EventType,
    collected_at: datetime,
    payload: dict,
) -> str:
    body = {
        "event_type": event_type.value,
        "collected_at": collected_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "payload": payload,
    }
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _serialize_event(event: Event) -> EventOut:
    return EventOut(
        event_id=event.event_uid,
        event_type=event.event_type.value,  # type: ignore[arg-type]
        collected_at=event.collected_at,
        temperature=event.temperature,
        giver=event.giver,
        receiver=event.receiver,
        received_at=event.received_at,
    )


def serialize_transfer(transfer: Transfer) -> TransferOut:
    return TransferOut(
        id=transfer.id,
        box_code=transfer.box_code,
        start_time=transfer.start_time,
        end_time=transfer.end_time,
        min_temp=transfer.min_temp,
        max_temp=transfer.max_temp,
        max_sample_interval_seconds=transfer.max_sample_interval_seconds,
        first_custodian=transfer.first_custodian,
        is_closed=transfer.is_closed,
        closed_at=transfer.closed_at,
        current_verdict=(
            transfer.current_verdict.value
            if transfer.current_verdict is not None
            else None
        ),
        created_at=transfer.created_at,
    )


def create_transfer(db: Session, data: TransferCreate) -> Transfer:
    transfer = Transfer(
        box_code=data.box_code,
        start_time=data.start_time.astimezone(timezone.utc),
        end_time=data.end_time.astimezone(timezone.utc),
        min_temp=data.min_temp,
        max_temp=data.max_temp,
        max_sample_interval_seconds=data.max_sample_interval_seconds,
        first_custodian=data.first_custodian,
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return transfer


def get_transfer_or_404(db: Session, transfer_id: int) -> Transfer:
    transfer = db.get(Transfer, transfer_id)
    if transfer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"转运 {transfer_id} 不存在",
        )
    return transfer


def _lock_transfer(db: Session, transfer: Transfer) -> Transfer:
    """对转运记录加行级写锁，串行化同一转运的补传/关闭/重算（PostgreSQL 生效）。"""
    locked = db.scalar(
        select(Transfer).where(Transfer.id == transfer.id).with_for_update()
    )
    return locked or transfer


def list_events(db: Session, transfer_id: int) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(Event.transfer_id == transfer_id)
            .order_by(Event.collected_at, Event.id)
        )
    )


def _ingest(
    db: Session,
    transfer: Transfer,
    event_type: EventType,
    event_id: str,
    collected_at: datetime,
    payload: dict,
) -> EventAccepted:
    transfer = _lock_transfer(db, transfer)
    collected_utc = collected_at.astimezone(timezone.utc)

    # 仅接收起止时间内（含边界）的事件
    if not (transfer.start_time <= collected_utc <= transfer.end_time):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "事件采集时间不在转运起止时间范围内（含边界）",
                "event_id": event_id,
                "collected_at": collected_utc.isoformat(),
                "start_time": transfer.start_time.isoformat(),
                "end_time": transfer.end_time.isoformat(),
            },
        )

    content_hash = _content_hash(event_type, collected_utc, payload)
    event = Event(
        transfer_id=transfer.id,
        event_uid=event_id,
        event_type=event_type,
        collected_at=collected_utc,
        content_hash=content_hash,
        **payload,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # 同 ID 已存在：同内容视为幂等重传，内容冲突则拒绝
        existing = db.scalar(
            select(Event).where(
                Event.transfer_id == transfer.id, Event.event_uid == event_id
            )
        )
        if existing is None:  # pragma: no cover - 理论上不会发生
            raise
        if existing.content_hash == content_hash:
            return EventAccepted(
                accepted=False, duplicate=True, event=_serialize_event(existing)
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "相同事件 ID 已存在但内容不同（冲突），原始事件不得修改",
                "event_id": event_id,
                "existing_type": existing.event_type.value,
            },
        )
    db.refresh(event)

    # 关闭后补传：追加一版重新核验的结论；关闭前不产生任何结论
    if transfer.is_closed:
        _recompute(db, transfer, trigger="supplement")

    return EventAccepted(
        accepted=True, duplicate=False, event=_serialize_event(event)
    )


def ingest_temperature(
    db: Session, transfer: Transfer, data: TemperatureEventCreate
) -> EventAccepted:
    return _ingest(
        db,
        transfer,
        EventType.temperature,
        data.event_id,
        data.collected_at,
        {"temperature": data.temperature},
    )


def ingest_handover(
    db: Session, transfer: Transfer, data: HandoverEventCreate
) -> EventAccepted:
    return _ingest(
        db,
        transfer,
        EventType.handover,
        data.event_id,
        data.collected_at,
        {"giver": data.giver, "receiver": data.receiver},
    )


def _recompute(db: Session, transfer: Transfer, trigger: str) -> ConclusionRecord:
    events = list_events(db, transfer.id)
    result = verify(transfer, events)

    last_version = db.scalar(
        select(ConclusionRecord.version)
        .where(ConclusionRecord.transfer_id == transfer.id)
        .order_by(ConclusionRecord.version.desc())
        .limit(1)
    )
    record = ConclusionRecord(
        transfer_id=transfer.id,
        version=(last_version or 0) + 1,
        verdict=result.verdict,
        trigger=trigger,
        evidence_event_ids=[e.id for e in events],
        event_count=len(events),
        temperature_count=sum(
            1 for e in events if e.event_type == EventType.temperature
        ),
        handover_count=sum(1 for e in events if e.event_type == EventType.handover),
    )
    record.reasons = [
        ConclusionReason(
            code=r.code,
            message=r.message,
            event_uid=r.event_uid,
            detail=r.detail,
        )
        for r in result.reasons
    ]
    db.add(record)
    transfer.current_verdict = result.verdict
    db.commit()
    db.refresh(record)
    return record


def close_transfer(db: Session, transfer: Transfer):
    transfer = _lock_transfer(db, transfer)
    if transfer.is_closed:
        return True, _current_conclusion(db, transfer)
    transfer.is_closed = True
    transfer.closed_at = utcnow()
    record = _recompute(db, transfer, trigger="close")
    return False, _record_to_out(db, record)


def _record_to_out(db: Session, record: ConclusionRecord) -> ConclusionOut:
    events_by_id = {
        e.id: e
        for e in db.scalars(
            select(Event).where(Event.id.in_(record.evidence_event_ids or [-1]))
        )
    }
    evidence = [
        _serialize_event(events_by_id[eid])
        for eid in record.evidence_event_ids
        if eid in events_by_id
    ]
    return ConclusionOut(
        version=record.version,
        verdict=record.verdict.value,  # type: ignore[arg-type]
        trigger=record.trigger,  # type: ignore[arg-type]
        reasons=[
            ReasonOut(
                code=r.code,
                message=r.message,
                event_id=r.event_uid,
                detail=r.detail,
            )
            for r in record.reasons
        ],
        event_count=record.event_count,
        temperature_count=record.temperature_count,
        handover_count=record.handover_count,
        evidence_event_ids=record.evidence_event_ids,
        evidence=evidence,
        computed_at=record.computed_at,
    )


def _current_conclusion(db: Session, transfer: Transfer) -> ConclusionOut:
    record = db.scalar(
        select(ConclusionRecord)
        .where(ConclusionRecord.transfer_id == transfer.id)
        .order_by(ConclusionRecord.version.desc())
        .limit(1)
    )
    if record is None:  # pragma: no cover - 关闭后必有记录
        raise HTTPException(status_code=500, detail="结论记录缺失")
    return _record_to_out(db, record)


def get_current_conclusion(db: Session, transfer: Transfer) -> ConclusionOut:
    if not transfer.is_closed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="转运尚未关闭，关闭前不得给出最终结论",
        )
    return _current_conclusion(db, transfer)


def list_conclusions(db: Session, transfer: Transfer) -> list[ConclusionOut]:
    records = db.scalars(
        select(ConclusionRecord)
        .where(ConclusionRecord.transfer_id == transfer.id)
        .order_by(ConclusionRecord.version)
    )
    return [_record_to_out(db, r) for r in records]
