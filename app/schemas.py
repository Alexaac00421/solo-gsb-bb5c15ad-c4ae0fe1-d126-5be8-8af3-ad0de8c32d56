from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransferCreate(StrictModel):
    box_code: str = Field(..., min_length=1, max_length=128, description="样本箱箱号")
    start_time: AwareDatetime = Field(..., description="转运开始时间（含时区，ISO 8601）")
    end_time: AwareDatetime = Field(..., description="转运结束时间（含时区，ISO 8601）")
    min_temp: float = Field(..., description="允许温度下限（含边界，摄氏度）")
    max_temp: float = Field(..., description="允许温度上限（含边界，摄氏度）")
    max_sample_interval_seconds: int = Field(
        ..., gt=0, description="最大采样间隔（秒），也用于读数距起止边界的允许时差"
    )
    first_custodian: str = Field(..., min_length=1, max_length=128, description="首位保管人")

    @model_validator(mode="after")
    def _validate_rules(self) -> "TransferCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time 必须晚于 start_time")
        if self.max_temp < self.min_temp:
            raise ValueError("max_temp 不能小于 min_temp")
        return self


class TemperatureEventCreate(StrictModel):
    event_id: str = Field(..., min_length=1, max_length=128, description="事件唯一 ID")
    collected_at: AwareDatetime = Field(..., description="采集时间（含时区，ISO 8601）")
    temperature: float = Field(..., description="温度读数（摄氏度）")


class HandoverEventCreate(StrictModel):
    event_id: str = Field(..., min_length=1, max_length=128, description="事件唯一 ID")
    collected_at: AwareDatetime = Field(..., description="采集时间（含时区，ISO 8601）")
    giver: str = Field(..., min_length=1, max_length=128, description="交出人")
    receiver: str = Field(..., min_length=1, max_length=128, description="接收人")


class TransferOut(BaseModel):
    id: int
    box_code: str
    start_time: datetime
    end_time: datetime
    min_temp: float
    max_temp: float
    max_sample_interval_seconds: int
    first_custodian: str
    is_closed: bool
    closed_at: datetime | None = None
    current_verdict: Literal["qualified", "unqualified", "evidence_pending"] | None = None
    created_at: datetime


class EventOut(BaseModel):
    event_id: str
    event_type: Literal["temperature", "handover"]
    collected_at: datetime
    temperature: float | None = None
    giver: str | None = None
    receiver: str | None = None
    received_at: datetime


class EventAccepted(BaseModel):
    accepted: bool
    duplicate: bool
    event: EventOut


class CloseResult(BaseModel):
    already_closed: bool
    conclusion: "ConclusionOut"


class ReasonOut(BaseModel):
    code: str
    message: str
    event_id: str | None = None
    detail: dict | None = None


class ConclusionOut(BaseModel):
    version: int
    verdict: Literal["qualified", "unqualified", "evidence_pending"]
    trigger: Literal["close", "supplement"]
    reasons: list[ReasonOut]
    event_count: int
    temperature_count: int
    handover_count: int
    evidence_event_ids: list[int]
    evidence: list[EventOut]
    computed_at: datetime


CloseResult.model_rebuild()
