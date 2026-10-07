#!/usr/bin/env python3
"""样本箱转运证据核验服务 —— HTTP API 调用示例（仅依赖标准库）。

运行：
    # 先启动服务：docker compose up --build
    BASE_URL=http://127.0.0.1:8000 python3 examples/api_demo.py

示例完整演示：
创建转运 -> 补传温度/交接 -> 重传幂等/冲突拒绝 -> 关闭核验
-> 关闭后补证重算 -> 查询当前结论、原因与历次结论依据。
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def request(method: str, path: str, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def show(title: str, status: int, payload) -> None:
    print(f"\n--- {title} [{status}] ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:2500])


def main() -> None:
    # 1. 创建转运：箱号 / 起止时间 / 允许温度区间 / 最大采样间隔 / 首位保管人
    status, transfer = request("POST", "/transfers", {
        "box_code": "BOX-A-20261007",
        "start_time": "2026-10-07T08:00:00+08:00",
        "end_time": "2026-10-07T10:00:00+08:00",
        "min_temp": 2.0,
        "max_temp": 8.0,
        "max_sample_interval_seconds": 600,
        "first_custodian": "Alice",
    })
    show("创建转运", status, transfer)
    tid = transfer["id"]

    # 2. 关闭前查询结论 → 409（关闭前不得给最终结论）
    show("关闭前查询结论（应 409）", *request("GET", f"/transfers/{tid}/conclusion"))

    # 3. 离线补传温度读数（唯一事件 ID + 采集时间）
    temps = [
        ("temp-0800", "2026-10-07T08:00:00+08:00", 5.1),
        ("temp-0805", "2026-10-07T08:05:00+08:00", 5.4),
        ("temp-0810", "2026-10-07T08:10:00+08:00", 4.9),
    ]
    for event_id, ts, value in temps:
        show(f"补传温度 {event_id}", *request(
            "POST", f"/transfers/{tid}/events/temperature",
            {"event_id": event_id, "collected_at": ts, "temperature": value},
        ))

    # 4. 离线补传交接记录（交出人 / 接收人）
    show("补传交接 1", *request(
        "POST", f"/transfers/{tid}/events/handover",
        {"event_id": "handover-1",
         "collected_at": "2026-10-07T08:15:00+08:00",
         "giver": "Alice", "receiver": "Bob"},
    ))

    # 5. 幂等：同 ID 同内容重传（即便用不同时区写法表达同一时刻）
    show("幂等重传（duplicate=true）", *request(
        "POST", f"/transfers/{tid}/events/temperature",
        {"event_id": "temp-0800",
         "collected_at": "2026-10-07T00:00:00+00:00",  # 与 08:00+08:00 同一时刻
         "temperature": 5.1},
    ))

    # 6. 冲突：同 ID 不同内容 → 409 拒绝
    show("同ID内容冲突（应 409）", *request(
        "POST", f"/transfers/{tid}/events/temperature",
        {"event_id": "temp-0800",
         "collected_at": "2026-10-07T08:00:00+08:00",
         "temperature": 9.9},
    ))

    # 7. 时间窗外事件 → 422 拒绝
    show("时间窗外事件（应 422）", *request(
        "POST", f"/transfers/{tid}/events/temperature",
        {"event_id": "temp-late",
         "collected_at": "2026-10-07T10:00:01+08:00",
         "temperature": 5.0},
    ))

    # 8. 关闭转运并核验（此时读数只到 08:10，距结束边界超 600 秒 → 不合格）
    show("关闭转运并核验", *request("POST", f"/transfers/{tid}/close", {}))

    # 9. 关闭后补传：补齐读数并补上剩余交接链，每次新增事件都会重算
    more_temps = [
        ("temp-0820", "2026-10-07T08:20:00+08:00", 5.0),
        ("temp-0900", "2026-10-07T09:00:00+08:00", 5.2),
    ]
    for event_id, ts, value in more_temps:
        request("POST", f"/transfers/{tid}/events/temperature",
                {"event_id": event_id, "collected_at": ts, "temperature": value})
    # 故意补一条越界读数，演示当前结论被重算为不合格及原因
    show("关闭后补传越界读数（重算为不合格）", *request(
        "POST", f"/transfers/{tid}/events/temperature",
        {"event_id": "temp-0910",
         "collected_at": "2026-10-07T09:10:00+08:00",
         "temperature": 9.1},
    ))

    # 10. 查询当前结论与原因
    show("当前结论", *request("GET", f"/transfers/{tid}/conclusion"))

    # 11. 查询历次结论及依据
    status, history = request("GET", f"/transfers/{tid}/conclusions")
    print(f"\n--- 历次结论（共 {len(history)} 版）---")
    for item in history:
        print(f"  v{item['version']:<3} {item['verdict']:<16} "
              f"触发={item['trigger']:<10} 事件数={item['event_count']}  "
              f"原因={[r['code'] for r in item['reasons']]}")

    # 12. 原始事件始终保留、可查
    status, events = request("GET", f"/transfers/{tid}/events")
    print(f"\n原始事件共 {len(events)} 条（不得删除）")


if __name__ == "__main__":
    main()
