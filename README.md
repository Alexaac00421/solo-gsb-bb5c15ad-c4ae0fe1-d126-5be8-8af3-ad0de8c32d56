# 样本箱转运证据核验服务

基于 **Python 3.11 + FastAPI + PostgreSQL** 的样本箱转运证据核验 HTTP 服务。
管理转运过程中离线补传的温度读数与交接记录，在转运关闭后按采集时间核验
温度链与交接链，给出 **合格 / 不合格 / 待补证** 结论；关闭后仍可补传并
自动重算，保留历次结论及其原始事件依据，原始事件永不删除。

## 一、一键启动（Docker Compose）

前置要求：已安装 Docker 与 Docker Compose 插件。

```bash
cd <项目根目录>
docker compose up --build
```

启动后：

- API 服务：<http://localhost:8000>
- 交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/health>

Compose 会自动完成：

1. 构建应用镜像并安装 `requirements.txt` 中的依赖；
2. 启动 PostgreSQL 16 并通过健康检查等待就绪；
3. 应用等待数据库连通后启动，并自动初始化全部数据表（`SQLAlchemy create_all`）。

停止并清理：

```bash
docker compose down            # 停止（保留数据卷）
docker compose down -v         # 停止并删除数据库数据卷
```

## 二、配置

通过环境变量配置（均可在项目根目录创建 `.env` 文件覆盖，见下表）：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `API_PORT` | `8000` | 宿主机映射的 API 端口 |
| `POSTGRES_USER` | `sample` | 数据库用户名 |
| `POSTGRES_PASSWORD` | `sample` | 数据库密码 |
| `POSTGRES_DB` | `sample_transfer` | 数据库名 |
| `POSTGRES_PORT` | `5432` | 宿主机映射的数据库端口 |
| `DATABASE_URL` | 见 compose | 数据库连接串（容器内指向 `db:5432`） |

示例 `.env`：

```env
API_PORT=18000
POSTGRES_PASSWORD=a-strong-password
POSTGRES_DB=sample_transfer
```

## 三、本地开发（无 Docker）

```bash
pip install -r requirements.txt
# 使用 PostgreSQL（推荐，与生产一致）
export DATABASE_URL="postgresql+psycopg2://sample:sample@127.0.0.1:5432/sample_transfer"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 或仅用于本地试跑 SQLite（时区语义与 PostgreSQL 一致）
export DATABASE_URL="sqlite:////tmp/sample.db"
uvicorn app.main:app --reload
```

## 四、业务规则

### 转运

创建转运需提供：箱号、起止时间、允许温度区间（含边界）、最大采样间隔（秒）、
首位保管人。校验规则：结束时间必须晚于开始时间、温度上限不低于下限、
采样间隔为正；所有时间必须带时区（ISO 8601，如 `2026-10-07T08:00:00+08:00`）。

### 事件补传（离线补传）

- 温度读数：`事件 ID + 采集时间 + 温度值`。
- 交接记录：`事件 ID + 采集时间 + 交出人 + 接收人`。
- **仅接收采集时间落在转运起止时间内（含边界）的事件**，窗外事件返回 `422`。
- **幂等**：同一转运下相同事件 ID、相同内容重传返回 `200` 且 `duplicate=true`，
  不产生重复数据（不同时区写法但表示同一时刻视为相同）。
- **冲突拒绝**：相同事件 ID 但内容不同，返回 `409`，原始事件保持不变。
- 转运关闭后仍可补传；每新增一条事件都会重新核验并追加一版结论。
  幂等重传不触发重算。
- 原始事件只追加、永不删除、永不修改。

### 关闭与核验

`POST /transfers/{id}/close` 关闭转运并首次核验；关闭前查询最终结论返回 `409`
（关闭前不得给最终结论）。核验按采集时间排序全部事件后检查：

1. **交接链**
   - `HANDOVER_START_MISMATCH`：首次交接的交出人不是首位保管人（起点不衔接）；
   - `HANDOVER_CHAIN_BREAK`：相邻交接中后一次交出人与前一次接收人不一致。
2. **温度链**
   - `TEMP_OUT_OF_RANGE`：读数越出允许温度区间（含边界值，等于边界不违规）；
   - `FIRST_READING_GAP_EXCEEDED`：首个读数距开始边界超过最大采样间隔；
   - `LAST_READING_GAP_EXCEEDED`：末个读数距结束边界超过最大采样间隔；
   - `SAMPLE_INTERVAL_EXCEEDED`：相邻读数间隔超过最大采样间隔。

结论：

- 任一违规发生 → `unqualified`（不合格，返回全部原因）；
- 没有任何温度读数且无交接违规 → `evidence_pending`（待补证，
  原因码 `NO_TEMPERATURE_READING`）；
- 其余情况 → `qualified`（合格）。

关闭后补传会重算当前结论；可查询**当前结论与原因**以及**历次结论依据**
（每版含结论、原因、事件统计、触发方式、计算时间与所依据的完整事件快照）。

## 五、HTTP API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `POST` | `/transfers` | 创建转运 |
| `GET` | `/transfers` | 转运列表 |
| `GET` | `/transfers/{id}` | 转运详情（含当前结论） |
| `POST` | `/transfers/{id}/events/temperature` | 补传温度读数 |
| `POST` | `/transfers/{id}/events/handover` | 补传交接记录 |
| `GET` | `/transfers/{id}/events` | 查询全部原始事件 |
| `POST` | `/transfers/{id}/close` | 关闭转运并核验（重复关闭幂等） |
| `GET` | `/transfers/{id}/conclusion` | 当前结论与原因（关闭前 `409`） |
| `GET` | `/transfers/{id}/conclusions` | 历次结论及每版依据 |

### 错误码约定

- `404`：转运不存在；
- `409`：同 ID 事件内容冲突；或转运尚未关闭即查询最终结论；
- `422`：参数无效（含事件采集时间超出转运起止时间窗）；
- `200` + `duplicate=true`：同 ID 同内容幂等重传。

## 六、调用示例

```bash
# curl 版（无需额外依赖）
BASE_URL=http://127.0.0.1:8000 sh examples/api_demo.sh

# Python 版（仅标准库）
BASE_URL=http://127.0.0.1:8000 python3 examples/api_demo.py
```

最小示例：

```bash
curl -sS -X POST http://127.0.0.1:8000/transfers \
  -H 'Content-Type: application/json' \
  -d '{
    "box_code": "BOX-001",
    "start_time": "2026-10-07T08:00:00+08:00",
    "end_time":   "2026-10-07T10:00:00+08:00",
    "min_temp": 2.0,
    "max_temp": 8.0,
    "max_sample_interval_seconds": 600,
    "first_custodian": "Alice"
  }'
```

## 七、项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由
│   ├── schemas.py       # 请求/响应模型与参数校验
│   ├── models.py        # SQLAlchemy 表模型
│   ├── crud.py          # 业务逻辑：事件接收、幂等、结论版本管理
│   ├── verification.py  # 核验规则（交接链 + 温度链）
│   └── database.py      # 引擎、会话、UTC 时间类型
├── examples/            # 调用示例（Python / curl）
├── e2e_test.py          # 端到端业务规则自测（36 项断言）
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh        # 等待数据库就绪后启动并自动建表
└── requirements.txt
```

## 八、自测

```bash
# 启动一个服务实例（PostgreSQL 或 SQLite 均可），然后：
BASE_URL=http://127.0.0.1:8000 python3 e2e_test.py
```
