#!/bin/sh
# 样本箱转运证据核验服务 —— curl 调用示例
# 用法：BASE_URL=http://127.0.0.1:8000 sh examples/api_demo.sh
set -eu

BASE="${BASE_URL:-http://127.0.0.1:8000}"

echo "== 1. 创建转运 =="
TRANSFER=$(curl -sS -X POST "$BASE/transfers" \
  -H 'Content-Type: application/json' \
  -d '{
    "box_code": "BOX-CURL-001",
    "start_time": "2026-10-07T08:00:00+08:00",
    "end_time":   "2026-10-07T10:00:00+08:00",
    "min_temp": 2.0,
    "max_temp": 8.0,
    "max_sample_interval_seconds": 600,
    "first_custodian": "Alice"
  }')
echo "$TRANSFER" | python3 -m json.tool
TID=$(echo "$TRANSFER" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "转运 ID = $TID"

echo "== 2. 关闭前查询结论（预期 409）=="
curl -sS -i "$BASE/transfers/$TID/conclusion" | head -n 1

echo "== 3. 补传温度读数 =="
curl -sS -X POST "$BASE/transfers/$TID/events/temperature" \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"t-0800","collected_at":"2026-10-07T08:00:00+08:00","temperature":5.2}'
echo

echo "== 4. 补传交接记录（Alice -> Bob）=="
curl -sS -X POST "$BASE/transfers/$TID/events/handover" \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"h-1","collected_at":"2026-10-07T08:10:00+08:00","giver":"Alice","receiver":"Bob"}'
echo

echo "== 5. 同 ID 同内容重传（预期 duplicate=true）=="
curl -sS -X POST "$BASE/transfers/$TID/events/temperature" \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"t-0800","collected_at":"2026-10-07T08:00:00+08:00","temperature":5.2}'
echo

echo "== 6. 同 ID 内容冲突（预期 409）=="
curl -sS -i -X POST "$BASE/transfers/$TID/events/temperature" \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"t-0800","collected_at":"2026-10-07T08:00:00+08:00","temperature":6.2}' | head -n 1

echo "== 7. 关闭转运并核验 =="
curl -sS -X POST "$BASE/transfers/$TID/close" -H 'Content-Type: application/json' \
  | python3 -m json.tool

echo "== 8. 查询当前结论与原因 =="
curl -sS "$BASE/transfers/$TID/conclusion" | python3 -m json.tool

echo "== 9. 查询历次结论依据 =="
curl -sS "$BASE/transfers/$TID/conclusions" | python3 -m json.tool

echo "== 10. 查询全部原始事件 =="
curl -sS "$BASE/transfers/$TID/events" | python3 -m json.tool
