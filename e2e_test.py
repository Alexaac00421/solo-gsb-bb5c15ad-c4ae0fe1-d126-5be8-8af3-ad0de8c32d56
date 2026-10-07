"""端到端业务规则自测：直接用标准库调用 HTTP API。

用法：BASE_URL=http://127.0.0.1:8000 python3 e2e_test.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8011")
PASS, FAIL = 0, 0


def call(method, path, body=None, expect=200):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        payload = json.loads(e.read() or b"null")
        if e.code != expect:
            raise AssertionError(f"{method} {path}: 期望 {expect}, 得到 {e.code}: {payload}")
        return e.code, payload


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {extra}")


def create(expect=200, **over):
    body = dict(
        box_code="BOX-001",
        start_time="2026-10-01T08:00:00+08:00",
        end_time="2026-10-01T10:00:00+08:00",
        min_temp=2.0,
        max_temp=8.0,
        max_sample_interval_seconds=600,
        first_custodian="Alice",
    )
    body.update(over)
    return call("POST", "/transfers", body, expect=expect)


print("== 1. 参数校验 ==")
s, t = create()
check("创建转运成功", s == 201 and t["is_closed"] is False and t["current_verdict"] is None)
s, _ = call("POST", "/transfers", {**dict(
    box_code="X", start_time="2026-10-01T10:00:00+08:00",
    end_time="2026-10-01T08:00:00+08:00", min_temp=2, max_temp=8,
    max_sample_interval_seconds=600, first_custodian="A")}, expect=422)
check("end<=start 被拒(422)", s == 422)
s, _ = create(max_temp=1.0, expect=422)
check("max<min 被拒(422)", s == 422)
s, _ = create(max_sample_interval_seconds=0, expect=422)
check("间隔<=0 被拒(422)", s == 422)
s, _ = create(box_code="", expect=422)
check("空箱号被拒(422)", s == 422)
s, _ = create(start_time="2026-10-01T08:00:00", expect=422)
check("无时区时间被拒(422)", s == 422)
s, _ = call("GET", "/transfers/9999", expect=404)
check("不存在转运 404", s == 404)

print("== 2. 关闭前不得给结论 ==")
s, _ = call("GET", f"/transfers/{t['id']}/conclusion", expect=409)
check("关闭前查询结论 409", s == 409)
s, hist = call("GET", f"/transfers/{t['id']}/conclusions")
check("关闭前历史结论为空", s == 200 and hist == [])

print("== 3. 时间窗（含边界）与幂等/冲突 ==")
s, _ = call("POST", f"/transfers/{t['id']}/events/temperature",
            {"event_id": "E-OUT", "collected_at": "2026-10-01T10:00:01+08:00",
             "temperature": 5.0}, expect=422)
check("窗外事件被拒(422)", s == 422)
s, r = call("POST", f"/transfers/{t['id']}/events/temperature",
            {"event_id": "E-START", "collected_at": "2026-10-01T08:00:00+08:00",
             "temperature": 5.0})
check("边界时刻事件接收", s == 200 and r["accepted"] is True and r["duplicate"] is False)
s, r = call("POST", f"/transfers/{t['id']}/events/temperature",
            {"event_id": "E-START", "collected_at": "2026-10-01T00:00:00+00:00",
             "temperature": 5.0})
check("同ID同内容重传幂等(不同时区表示等价)", s == 200 and r["duplicate"] is True)
s, _ = call("POST", f"/transfers/{t['id']}/events/temperature",
            {"event_id": "E-START", "collected_at": "2026-10-01T08:00:00+08:00",
             "temperature": 6.0}, expect=409)
check("同ID内容冲突 409", s == 409)
s, _ = call("POST", f"/transfers/{t['id']}/events/temperature",
            {"event_id": "E-BAD", "collected_at": "2026-10-01T09:00:00+08:00",
             "temperature": "热"}, expect=422)
check("非法读数被拒(422)", s == 422)
s, evs = call("GET", f"/transfers/{t['id']}/events")
check("冲突事件未污染原始数据(仍只有1条)", s == 200 and len(evs) == 1)

print("== 4. 关闭 → 待补证（无读数以外…此例已有读数，先造纯交接案例）==")
s, t2 = create(box_code="BOX-002", first_custodian="Alice")
call("POST", f"/transfers/{t2['id']}/events/handover",
     {"event_id": "H1", "collected_at": "2026-10-01T08:30:00+08:00",
      "giver": "Alice", "receiver": "Bob"})
call("POST", f"/transfers/{t2['id']}/events/handover",
     {"event_id": "H2", "collected_at": "2026-10-01T09:10:00+08:00",
      "giver": "Bob", "receiver": "Carol"})
s, r = call("POST", f"/transfers/{t2['id']}/close", {})
check("关闭返回待补证", s == 200 and r["conclusion"]["verdict"] == "evidence_pending")
codes = {x["code"] for x in r["conclusion"]["reasons"]}
check("待补证原因为 NO_TEMPERATURE_READING", codes == {"NO_TEMPERATURE_READING"})
s, r2 = call("POST", f"/transfers/{t2['id']}/close", {})
check("重复关闭幂等(already_closed)", s == 200 and r2["already_closed"] is True
      and r2["conclusion"]["version"] == 1)

print("== 5. 关闭后补传 → 重算并保留历次依据 ==")
# 每 5 分钟一个读数，覆盖起止边界，满足 600 秒间隔
from datetime import datetime, timedelta, timezone

_base = datetime(2026, 10, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
for i in range(25):
    ts = (_base + timedelta(minutes=5 * i)).isoformat()
    call("POST", f"/transfers/{t2['id']}/events/temperature",
         {"event_id": f"T{i:02d}", "collected_at": ts,
          "temperature": 5.0 + (i % 3)})
s, cur = call("GET", f"/transfers/{t2['id']}/conclusion")
n_readings = 25
check("补传后当前结论=合格(版本随每个新增事件递增)", s == 200
      and cur["verdict"] == "qualified" and cur["version"] == 1 + n_readings, cur)
check("合格无违规原因", cur["reasons"] == [])
s, hist = call("GET", f"/transfers/{t2['id']}/conclusions")
check("每次新增补传都追加结论版本", len(hist) == 1 + n_readings)
check("v1待补证依据含2条交接", hist[0]["handover_count"] == 2
      and hist[0]["temperature_count"] == 0 and len(hist[0]["evidence"]) == 2)
check("中间版本可追溯(第2版含1读数)", hist[1]["event_count"] == 3
      and hist[1]["temperature_count"] == 1)
check("最新版依据含全部事件", hist[-1]["event_count"] == 2 + n_readings
      and len(hist[-1]["evidence"]) == 2 + n_readings)
s, dup = call("POST", f"/transfers/{t2['id']}/events/temperature",
              {"event_id": "T00",
               "collected_at": "2026-10-01T08:00:00+08:00", "temperature": 5.0})
check("关闭后幂等重传不产生新版本", dup["duplicate"] is True)
s, cur = call("GET", f"/transfers/{t2['id']}/conclusion")
check("版本保持不变", cur["version"] == 1 + n_readings)

print("== 6. 不合格场景 ==")
s, t3 = create(box_code="BOX-003")
# 交接起点不衔接 + 交接链断裂
call("POST", f"/transfers/{t3['id']}/events/handover",
     {"event_id": "H1", "collected_at": "2026-10-01T08:20:00+08:00",
      "giver": "Mallory", "receiver": "Bob"})  # 起点错
call("POST", f"/transfers/{t3['id']}/events/handover",
     {"event_id": "H2", "collected_at": "2026-10-01T08:50:00+08:00",
      "giver": "Alice", "receiver": "Carol"})  # 与上一接收人 Bob 不衔接
# 越界读数、首读数距边界超限、相邻间隔超限、末读数正常
call("POST", f"/transfers/{t3['id']}/events/temperature",
     {"event_id": "T1", "collected_at": "2026-10-01T08:20:00+08:00", "temperature": 5.0})
call("POST", f"/transfers/{t3['id']}/events/temperature",
     {"event_id": "T2", "collected_at": "2026-10-01T08:40:00+08:00", "temperature": 9.5})
call("POST", f"/transfers/{t3['id']}/events/temperature",
     {"event_id": "T3", "collected_at": "2026-10-01T09:55:00+08:00", "temperature": 3.0})
s, r = call("POST", f"/transfers/{t3['id']}/close", {})
codes = {x["code"] for x in r["conclusion"]["reasons"]}
check("不合格判定", r["conclusion"]["verdict"] == "unqualified", codes)
for code in ["HANDOVER_START_MISMATCH", "HANDOVER_CHAIN_BREAK", "TEMP_OUT_OF_RANGE",
             "FIRST_READING_GAP_EXCEEDED", "SAMPLE_INTERVAL_EXCEEDED"]:
    check(f"原因包含 {code}", code in codes, codes)
check("末读数距边界未超限(不误报)", "LAST_READING_GAP_EXCEEDED" not in codes)

print("== 7. 末读数距边界超限（独立）==")
s, t4 = create(box_code="BOX-004")
call("POST", f"/transfers/{t4['id']}/events/temperature",
     {"event_id": "T1", "collected_at": "2026-10-01T08:00:00+08:00", "temperature": 5.0})
call("POST", f"/transfers/{t4['id']}/events/temperature",
     {"event_id": "T2", "collected_at": "2026-10-01T08:05:00+08:00", "temperature": 5.0})
s, r = call("POST", f"/transfers/{t4['id']}/close", {})
codes = {x["code"] for x in r["conclusion"]["reasons"]}
check("仅末读数距边界超限", r["conclusion"]["verdict"] == "unqualified"
      and codes == {"LAST_READING_GAP_EXCEEDED"}, codes)

print("== 8. 原始事件保留 + 边界读数合格 ==")
s, evs = call("GET", f"/transfers/{t3['id']}/events")
check("原始事件全部保留(5条)", len(evs) == 5)
s, t5 = create(box_code="BOX-005",
               start_time="2026-10-01T08:00:00+08:00",
               end_time="2026-10-01T08:10:00+08:00")
call("POST", f"/transfers/{t5['id']}/events/temperature",
     {"event_id": "T-START", "collected_at": "2026-10-01T08:00:00+08:00",
      "temperature": 8.0})
call("POST", f"/transfers/{t5['id']}/events/temperature",
     {"event_id": "T-END", "collected_at": "2026-10-01T08:10:00+08:00",
      "temperature": 2.0})
s, r = call("POST", f"/transfers/{t5['id']}/close", {})
check("两端边界读数取到上下限且无交接→合格",
      r["conclusion"]["verdict"] == "qualified", r["conclusion"]["reasons"])

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
