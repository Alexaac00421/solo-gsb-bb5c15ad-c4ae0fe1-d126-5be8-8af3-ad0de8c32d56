from __future__ import annotations

from fastapi import Depends, FastAPI, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import crud
from .database import Base, engine, get_db
from .models import Transfer
from .schemas import (
    CloseResult,
    ConclusionOut,
    EventAccepted,
    EventOut,
    HandoverEventCreate,
    TemperatureEventCreate,
    TransferCreate,
    TransferOut,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="样本箱转运证据核验服务",
    version="1.0.0",
    description=(
        "管理样本箱转运的离线补传证据（温度读数与交接记录），"
        "关闭转运后核验温度链与交接链，给出合格 / 不合格 / 待补证结论，"
        "并保留历次结论依据。"
    ),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/transfers", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
def create_transfer(
    data: TransferCreate, db: Session = Depends(get_db)
) -> TransferOut:
    return crud.serialize_transfer(crud.create_transfer(db, data))


@app.get("/transfers", response_model=list[TransferOut])
def list_transfers(db: Session = Depends(get_db)) -> list[TransferOut]:
    transfers = db.scalars(select(Transfer).order_by(Transfer.id))
    return [crud.serialize_transfer(t) for t in transfers]


@app.get("/transfers/{transfer_id}", response_model=TransferOut)
def get_transfer(
    transfer_id: int, db: Session = Depends(get_db)
) -> TransferOut:
    return crud.serialize_transfer(crud.get_transfer_or_404(db, transfer_id))


@app.post(
    "/transfers/{transfer_id}/events/temperature",
    response_model=EventAccepted,
)
def add_temperature(
    transfer_id: int,
    data: TemperatureEventCreate,
    db: Session = Depends(get_db),
) -> EventAccepted:
    transfer = crud.get_transfer_or_404(db, transfer_id)
    return crud.ingest_temperature(db, transfer, data)


@app.post(
    "/transfers/{transfer_id}/events/handover",
    response_model=EventAccepted,
)
def add_handover(
    transfer_id: int,
    data: HandoverEventCreate,
    db: Session = Depends(get_db),
) -> EventAccepted:
    transfer = crud.get_transfer_or_404(db, transfer_id)
    return crud.ingest_handover(db, transfer, data)


@app.get(
    "/transfers/{transfer_id}/events",
    response_model=list[EventOut],
)
def list_transfer_events(
    transfer_id: int, db: Session = Depends(get_db)
) -> list[EventOut]:
    transfer = crud.get_transfer_or_404(db, transfer_id)
    return [crud._serialize_event(e) for e in crud.list_events(db, transfer.id)]


@app.post(
    "/transfers/{transfer_id}/close",
    response_model=CloseResult,
)
def close_transfer(
    transfer_id: int, db: Session = Depends(get_db)
) -> CloseResult:
    transfer = crud.get_transfer_or_404(db, transfer_id)
    already_closed, conclusion = crud.close_transfer(db, transfer)
    return CloseResult(already_closed=already_closed, conclusion=conclusion)


@app.get(
    "/transfers/{transfer_id}/conclusion",
    response_model=ConclusionOut,
)
def get_current_conclusion(
    transfer_id: int, db: Session = Depends(get_db)
) -> ConclusionOut:
    transfer = crud.get_transfer_or_404(db, transfer_id)
    return crud.get_current_conclusion(db, transfer)


@app.get(
    "/transfers/{transfer_id}/conclusions",
    response_model=list[ConclusionOut],
)
def list_conclusions(
    transfer_id: int, db: Session = Depends(get_db)
) -> list[ConclusionOut]:
    transfer = crud.get_transfer_or_404(db, transfer_id)
    return crud.list_conclusions(db, transfer)
