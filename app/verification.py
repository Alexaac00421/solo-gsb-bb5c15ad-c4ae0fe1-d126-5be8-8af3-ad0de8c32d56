"""关闭转运后的证据核验逻辑。

核验仅依据已接收（采集时间落在 [start_time, end_time] 内）的原始事件，
按采集时间排序后进行：

1. 交接链：首次交接的交出人必须是首位保管人（起点衔接）；
   相邻交接中，后一次的交出人必须等于前一次的接收人（人员衔接）。
2. 温度读数：不得越出允许温度区间；首/末读数距起止边界、
   相邻读数之间的间隔均不得超过最大采样间隔。

任一违规即不合格；没有读数且无交接违规时为待补证；其余合格。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Event, EventType, Transfer, Verdict


@dataclass
class Reason:
    code: str
    message: str
    event_uid: str | None = None
    detail: dict | None = None


@dataclass
class VerificationResult:
    verdict: Verdict
    reasons: list[Reason] = field(default_factory=list)


def _ordered(events: list[Event]) -> list[Event]:
    # 同一采集时间时以入库主键排序，保证顺序确定
    return sorted(events, key=lambda e: (e.collected_at, e.id))


def verify(transfer: Transfer, events: list[Event]) -> VerificationResult:
    ordered = _ordered(events)
    readings = [e for e in ordered if e.event_type == EventType.temperature]
    handovers = [e for e in ordered if e.event_type == EventType.handover]
    reasons: list[Reason] = []

    # ---- 1. 交接链核验 ----
    expected_custodian = transfer.first_custodian
    for index, h in enumerate(handovers):
        if h.giver != expected_custodian:
            if index == 0:
                reasons.append(
                    Reason(
                        code="HANDOVER_START_MISMATCH",
                        message=(
                            f"交接链起点不衔接：交接交出人 '{h.giver}' "
                            f"与首位保管人 '{transfer.first_custodian}' 不一致"
                        ),
                        event_uid=h.event_uid,
                        detail={
                            "expected": transfer.first_custodian,
                            "actual": h.giver,
                        },
                    )
                )
            else:
                prev = handovers[index - 1]
                reasons.append(
                    Reason(
                        code="HANDOVER_CHAIN_BREAK",
                        message=(
                            f"交接人员不衔接：交接交出人 '{h.giver}' 与上一交接的"
                            f"接收人 '{prev.receiver}' 不一致"
                        ),
                        event_uid=h.event_uid,
                        detail={
                            "previous_event_id": prev.event_uid,
                            "expected": prev.receiver,
                            "actual": h.giver,
                        },
                    )
                )
        expected_custodian = h.receiver

    # ---- 2. 温度读数核验 ----
    limit = transfer.max_sample_interval_seconds

    for r in readings:
        if r.temperature < transfer.min_temp or r.temperature > transfer.max_temp:
            reasons.append(
                Reason(
                    code="TEMP_OUT_OF_RANGE",
                    message=(
                        f"温度读数 {r.temperature}℃ 越出允许区间 "
                        f"[{transfer.min_temp}, {transfer.max_temp}]℃"
                    ),
                    event_uid=r.event_uid,
                    detail={
                        "temperature": r.temperature,
                        "min_temp": transfer.min_temp,
                        "max_temp": transfer.max_temp,
                    },
                )
            )

    if readings:
        first, last = readings[0], readings[-1]

        first_gap = (first.collected_at - transfer.start_time).total_seconds()
        if first_gap > limit:
            reasons.append(
                Reason(
                    code="FIRST_READING_GAP_EXCEEDED",
                    message=(
                        f"首个读数距开始边界 {first_gap:g} 秒，"
                        f"超过最大允许间隔 {limit} 秒"
                    ),
                    event_uid=first.event_uid,
                    detail={"gap_seconds": first_gap, "limit_seconds": limit},
                )
            )

        last_gap = (transfer.end_time - last.collected_at).total_seconds()
        if last_gap > limit:
            reasons.append(
                Reason(
                    code="LAST_READING_GAP_EXCEEDED",
                    message=(
                        f"末个读数距结束边界 {last_gap:g} 秒，"
                        f"超过最大允许间隔 {limit} 秒"
                    ),
                    event_uid=last.event_uid,
                    detail={"gap_seconds": last_gap, "limit_seconds": limit},
                )
            )

        for prev, curr in zip(readings, readings[1:]):
            gap = (curr.collected_at - prev.collected_at).total_seconds()
            if gap > limit:
                reasons.append(
                    Reason(
                        code="SAMPLE_INTERVAL_EXCEEDED",
                        message=(
                            f"相邻读数间隔 {gap:g} 秒，超过最大采样间隔 {limit} 秒"
                        ),
                        event_uid=curr.event_uid,
                        detail={
                            "previous_event_id": prev.event_uid,
                            "gap_seconds": gap,
                            "limit_seconds": limit,
                        },
                    )
                )

    # ---- 3. 结论 ----
    if reasons:
        return VerificationResult(Verdict.unqualified, reasons)

    if not readings:
        return VerificationResult(
            Verdict.evidence_pending,
            [
                Reason(
                    code="NO_TEMPERATURE_READING",
                    message="时间窗内没有温度读数，且无交接违规，需补传读数后重新核验",
                )
            ],
        )

    return VerificationResult(Verdict.qualified, [])
