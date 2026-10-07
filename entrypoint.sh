#!/bin/sh
# 等待 PostgreSQL 就绪后再启动应用；表结构由 SQLAlchemy 自动初始化
set -e

echo "等待数据库就绪..."
python - <<'PY'
import os
import time

import psycopg2

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")

deadline = time.time() + 60
while True:
    try:
        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
        print("数据库已就绪")
        break
    except Exception as exc:  # noqa: BLE001
        if time.time() > deadline:
            raise
        print(f"数据库尚未就绪：{exc}，1 秒后重试")
        time.sleep(1)
PY

exec "$@"
