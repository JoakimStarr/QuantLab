---
title: 开发手册
slug: development
order: 3
group: 开发
summary: 环境准备、项目结构、配置、启动流程、代码规范、部署与常见问题
---

# 开发手册

> 本文档面向 QuantLab 开发者，覆盖环境搭建、项目结构、配置、启动、规范、部署与排障。
> 所有命令与字段均来自实际代码，WSL Ubuntu 为主开发环境。

---

## 1. 环境准备

### 1.1 版本要求

| 组件 | 版本要求 | 来源 |
|------|---------|------|
| Python | **3.11**（pyproject `target-version="py311"`，qlib 推荐 3.11） | `pyproject.toml` |
| Node.js | ≥ 18（Vite 5.3 要求） | `frontend/package.json` |
| npm | 随 Node | — |
| OS | WSL2 Ubuntu（推荐）/ Linux | `start.sh` 为 bash 脚本 |

### 1.2 WSL Ubuntu 系统依赖

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip build-essential nodejs npm
# 可选：git、make、tar（chenditc 解压用）
sudo apt install -y git tar
```

### 1.3 后端依赖安装

```bash
cd ~/QuantLab
python3.11 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
```

**关键依赖约束**（见 `requirements.txt`）：
- `akshare==1.18.63`：版本锁定，接口变动频繁，升级需测试
- `pyqlib>=0.9.6` + `protobuf<4` + `setuptools<81`：qlib 依赖 mlflow，需约束 protobuf（5.x 移除 service 模块）与 setuptools（83 移除 pkg_resources）
- `gplearn>=0.4.2`：符号回归
- `cvxpy>=1.5`：组合优化
- `bcrypt>=4.0` + `slowapi>=0.1.9`：口令哈希与限流

### 1.4 前端依赖安装

```bash
cd ~/QuantLab/frontend
npm install
```

主要依赖：Vue 3.4、Element Plus 2.7、ECharts 5.5、vue-router 4.3、pinia 2.1、axios、markdown-it、highlight.js。

### 1.5 AI Provider Key（三选一，支持故障转移）

复制 `.env.example` 为 `.env`，填入至少一个：
```bash
GLM_API_KEY=...
SILICONFLOW_API_KEY=...
OPENCODEZEN_API_KEY=...
```

---

## 2. 项目结构

```
QuantLab/
├── backend/                     后端（FastAPI + SQLAlchemy + qlib）
│   ├── app/
│   │   ├── main.py              FastAPI 入口（lifespan/健康检查/SPA 兜底/WebSocket）
│   │   ├── api/                 路由层（按模块分文件）
│   │   │   ├── router.py        聚合路由，prefix=/api/v1
│   │   │   ├── auth.py          认证（登录/状态/me/ai-status）
│   │   │   ├── config.py        运行时配置
│   │   │   ├── docs.py          技术文档列表/详情
│   │   │   ├── quant_data.py    量化数据同步/状态
│   │   │   ├── data_ext.py      数据扩展（进度/预览/历史/数据源/EOD/指数/行业/完整性）
│   │   │   ├── factor.py        因子 CRUD/评价/种子
│   │   │   ├── factor_ext.py    因子对比/衰减/导出/分层/中性化/深度分析
│   │   │   ├── strategy.py      策略 CRUD/回测/结果
│   │   │   ├── strategy_ext.py  参数扫描/回测对比/交易明细/walk-forward
│   │   │   ├── mining.py        挖掘任务（llm/symbolic/automl/text）
│   │   │   ├── mining_ext.py    挖掘模板
│   │   │   ├── market.py        市场行情（指数K线/概览）
│   │   │   └── logs.py          日志查询
│   │   ├── core/                基础设施（config/auth/database/middleware/scheduler/ratelimit/recovery/executor/websocket_manager/logging_config/errors）
│   │   ├── models/              ORM 模型（factor/strategy/backtest_result/mining_task/...）
│   │   ├── schemas/             Pydantic 模型（common.ApiResponse / quant.SyncDataRequest）
│   │   └── services/            业务层
│   │       ├── data/            数据采集层（详见 DATA_LAYER.md）
│   │       ├── factor/          因子库/表达式沙箱/Alpha158/中性化/正交化/对比
│   │       ├── quant/           因子评价/回测引擎/QLib回测/组合/优化器/walk-forward
│   │       ├── mining/          LLM/符号回归/AutoML/文本因子挖掘
│   │       ├── strategy/        策略管理/参数扫描/回测状态
│   │       ├── ai/              LLM 客户端 + Provider 路由
│   │       ├── task/            定时任务注册
│   │       └── docs/            文档加载器
│   ├── migrations/              Alembic 迁移
│   ├── tests/                   pytest 测试
│   ├── alembic.ini
│   └── Dockerfile
├── frontend/                    前端（Vue3 + Vite + Element Plus）
│   ├── src/                     api/components/composables/config/router/stores/styles/utils/views
│   ├── package.json
│   ├── vite.config.js
│   ├── nginx.conf               生产反向代理配置
│   └── Dockerfile
├── docs/                        技术文档（本文件所在）
├── data/                        QLib bin / SQLite / processed / models / industry_map.json
├── models/                      模型产物
├── logs/                        日志输出
├── config.yaml                  主配置
├── .env / .env.example          环境变量
├── docker-compose.yml
├── start.sh                     一键启动脚本（dev/docker）
├── requirements.txt
├── pyproject.toml               ruff/pytest/mypy 配置
└── pytest.ini
```

### 模块职责一句话

| 层 | 职责 |
|----|------|
| `api/` | HTTP 路由，参数校验，统一 `ApiResponse` 包装，后台任务调度 |
| `core/` | 配置、鉴权、数据库、中间件、限流、调度器、任务恢复、进程池 |
| `models/` | SQLAlchemy ORM 表定义 |
| `services/data/` | 多源数据采集与 QLib bin 落地 |
| `services/factor/` | 因子表达式安全沙箱、库 CRUD、中性化、正交化 |
| `services/quant/` | 因子评价、回测引擎、组合分析、walk-forward |
| `services/mining/` | 四种挖掘方式（LLM/符号回归/AutoML/文本） |
| `services/ai/` | 多 Provider 路由与故障转移 |

---

## 3. 配置说明

### 3.1 `config.yaml` 字段详解

| 顶层段 | 字段 | 默认值 | 说明 |
|--------|------|--------|------|
| `app` | `name` | QuantLab | 应用名 |
| `app` | `version` | 0.9.0-beta.1 | 版本号 |
| `app` | `timezone` | Asia/Shanghai | 应用时区 |
| `app` | `debug` | false | 调试开关 |
| `api` | `version` | v1 | API 版本前缀 |
| `api` | `request_timeout` | 30 | 请求超时（秒） |
| `api` | `cors_origins` | localhost:3000 等 | CORS 来源（可被环境变量覆盖） |
| `data` | `db_path` | data/quantlab.db | SQLite 路径 |
| `data` | `qlib_provider_uri`（在 quant 段） | data/qlib_bin/cn_data | QLib bin 目录 |
| `quant` | `data_source` | akshare | 数据源（chenditc / akshare） |
| `quant` | `universe` | csi300 | 股票池 |
| `quant` | `benchmark` | SH000300 | 基准指数 |
| `quant` | `adjust` | qfq | 复权方式 |
| `quant` | `topk` | 50 | 选股数 |
| `quant` | `n_drop` | 5 | 每期剔除数 |
| `quant` | `include_bj` | false | 是否含北交所 |
| `quant` | `cost_buy` / `cost_sell` | 0.0013 / 0.0023 | 买卖成本 |
| `quant` | `slippage_bps` | 5 | 滑点（基点），0=关闭 |
| `quant` | `fetch_interval_seconds` | 1.2 | akshare 请求间隔 |
| `quant` | `fetch_max_workers` | 3 | akshare 并发 |
| `quant` | `default_backtest_period` | 2020-01-01 ~ 2024-12-31 | 默认回测区间 |
| `quant.portfolio_optimizer` | `enabled` / `method` / `max_weight` / `max_industry_exposure` / `risk_aversion` | false / mean_variance / 0.05 / 0.2 / 0.5 | 组合优化配置 |
| `mining.llm` | `allowed_ops` | Ref/Mean/Std/.../EMA + 字段 | LLM 因子表达式算子白名单 |
| `mining.llm` | `candidates_per_run` | 10 | 每轮生成候选数 |
| `mining.llm` | `ic_threshold` | 0.03 | IC 达标阈值 |
| `mining.llm` | `eval_timeout_seconds` | 60 | 单因子 IC 评价超时 |
| `mining.symbolic` | `population` / `generations` / `tournament_size` / `parsimony_coefficient` | 1000 / 30 / 20 / 0.001 | gplearn 参数 |
| `mining.text` | `max_news_per_day` / `sentiment_labels` | 50 / [positive,neutral,negative] | 文本因子配置 |
| `mining.automl` | `combo_method` | lightgbm | AutoML 方法 |
| `scheduler` | `quant_data_update_time` | 18:00 | 定时同步时间 |
| `task` | `max_concurrent` | 2 | 挖掘任务并发上限 |
| `task` | `cpu_workers` / `io_workers` | 4 / 8 | 进程池/线程池大小 |
| `task.timeouts` | `llm` / `llm_hard_limit_seconds` / `symbolic` / `text` / `automl` / `optimize` | 300 / 7200 / 1800 / 900 / 600 / 600 | 任务分级超时 |
| `ai_provider` | `primary` / `fallback` / `tertiary` | opencodezen / glm / siliconflow | 三级 Provider 故障转移 |
| `ai_provider` | `total_timeout_seconds` / `route_budget_seconds` | 10 / 120 | 路由总预算 |
| `logging` | `level` / `dir` / `handlers` | INFO / logs / ... | 日志配置 |

### 3.2 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_ENV` | development | 环境（development 时关鉴权、开文档；其他视为生产） |
| `AUTH_ENABLED` | 按 APP_ENV 判定 | 鉴权开关（1/true/yes/on）；未设置时 development=关，其他=开 |
| `SECRET_KEY` | change_this_to_random_string | JWT 签名密钥；生产强制非默认值 |
| `ADMIN_PASSWORD` | admin123 | 管理员明文口令；生产强制非默认 |
| `ADMIN_PASSWORD_HASH` | 空 | bcrypt 哈希（推荐，优先于明文） |
| `LOGIN_RATE_LIMIT` | 5/minute | 登录限流（slowapi） |
| `CORS_ORIGINS` | 空 | 逗号分隔的 CORS 来源，覆盖 config |
| `PROJECT_ROOT` | 代码目录上四级 | 项目根（Docker 设 /app） |
| `STATIC_DIR` | static | 前端静态目录（Docker 设 /app/static） |
| `TZ` | — | 时区（Docker 设 Asia/Shanghai） |
| `GLM_API_KEY` / `SILICONFLOW_API_KEY` / `OPENCODEZEN_API_KEY` | 空 | AI Provider Key |
| `VITE_API_BASE_URL` | http://localhost:8000/api/v1 | 前端 API 基址 |
| `VITE_APP_TITLE` | 量化策略研究平台 | 前端标题 |

### 3.3 QLib 数据路径配置

- 配置项：`config.quant.qlib_provider_uri`（默认 `data/qlib_bin/cn_data`）
- 绝对路径：`settings.qlib_provider_path = PROJECT_ROOT / qlib_provider_uri`
- 数据同步通过 `PUT /api/v1/quant/data/data-source` 切换 chenditc/akshare
- 健康检查 `GET /health` 会检测 qlib 可用性与 `calendars/day.txt` 时间范围

---

## 4. 启动流程

### 4.1 一键启动（推荐）

```bash
cd ~/QuantLab
./start.sh            # dev 模式（默认）
./start.sh docker     # docker 模式
```

`start.sh dev` 会：
1. 检查 8000/3000 端口占用
2. 检查 `.venv` 与 `node_modules`，缺失则安装
3. 后台启动 `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`（cwd=backend）
4. 后台启动 `npm run dev -- --port 3000`（cwd=frontend）
5. 等待端口就绪后输出访问地址

### 4.2 后端手动启动

```bash
cd ~/QuantLab/backend
~/QuantLab/.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**lifespan 启动序列**（`app/main.py`）：
1. `setup_logging()` 日志初始化
2. `warn_insecure_config()` 安全告警
3. `settings.enforce_production_security()` 生产安全闸门（非 development 时强制）
4. `init_db()` 建表 + Alembic 迁移
5. `recover_stale_sync()` / `recover_stale_mining()` 恢复卡死任务
6. `rerun_pending_mining()` 重跑 pending 挖掘任务
7. `start_scheduler()` 启动 APScheduler

> 生产环境（APP_ENV≠development）会关闭 `/docs`、`/redoc`、`/openapi.json`。

### 4.3 前端启动

```bash
cd ~/QuantLab/frontend
npm run dev      # 开发（Vite 热更新，端口 3000）
npm run build    # 生产构建到 dist/，由后端 StaticFiles 服务
npm run lint     # ESLint 检查
npm run format   # Prettier 格式化
```

### 4.4 数据库初始化（自动）

`init_db()` 流程（`core/database.py`）：
1. `Base.metadata.create_all` 创建不存在的表
2. 子线程执行 `alembic upgrade head`（失败仅告警不阻断）
3. 每个连接设置 SQLite PRAGMA（WAL/busy_timeout/foreign_keys）

手动迁移：
```bash
cd ~/QuantLab/backend
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "描述"
```

### 4.5 定时任务

`start_scheduler()` → `register_scheduled_jobs(scheduler)`（`task/update_service.py`）：

| 任务 | 触发 | 说明 |
|------|------|------|
| `daily_quant_data_update` | cron 工作日 18:00 | qlib 数据增量同步（含 3 次重试，间隔 10 分钟） |
| `factor_decay_check` | cron 工作日 18:05 | 因子衰减检测（错开 5 分钟） |
| `reap_stale_mining` | interval 每 10 分钟 | 回收僵尸挖掘任务 |

---

## 5. 开发规范

### 5.1 Python 规范

- **异步**：IO 用 `async/await`；CPU 密集（因子评价/回测/CV 训练）走 `run_cpu` 进程池或 `run_in_executor` 线程池，不阻塞事件循环
- **类型注解**：公开函数必加；`pyproject.toml` mypy `python_version=3.11`
- **Lint**：ruff，`line-length=120`，启用 E/W/F/I/UP/B（**pyflakes 0 警告基线**）
  ```bash
  ruff check backend/app
  ruff format backend/app
  ```
- **依赖**：不假设库存在，新增依赖先入 `requirements.txt`

### 5.2 前端规范

- **Vue3 Composition API + `<script setup>`**
- **UI**：Element Plus + ECharts（vue-echarts）
- **状态**：Pinia
- **路由**：vue-router 4
- **Lint**：`npm run lint`（ESLint 9 + eslint-plugin-vue）
- **格式化**：`npm run format`（Prettier，配置见 `.prettierrc.json`）

### 5.3 API 响应格式

所有业务接口统一用 `ApiResponse` 包装（`schemas/common.py`）：

```python
class ApiResponse(BaseModel, Generic[T]):
    ok: bool
    data: Optional[T] = None
    error: Optional[dict] = None
```

**成功**：
```json
{"ok": true, "data": {...}, "error": null}
```

**失败**（`AppError` / 校验 / HTTPException 统一处理，见 `core/errors.py`）：
```json
{"ok": false, "data": null, "error": {"code": "VALIDATION_ERROR", "message": "...", "status": 422}}
```

常见错误码：`VALIDATION_ERROR`(422)、`NOT_FOUND`(404)、`QLIB_NOT_AVAILABLE`(503)、`AUTH_FAILED`(401)、`SYNC_IN_PROGRESS`(409)、`INTERNAL_ERROR`(500)。

### 5.4 测试

- 框架：pytest，`asyncio_mode=auto`，`pythonpath=["backend"]`
- 测试目录：`backend/tests/`（test_auth/test_backtest_engine/test_expression/test_factor_eval/test_symbolic）
- **基线：112 tests collected**（`pytest --collect-only`）
- 运行：
  ```bash
  cd ~/QuantLab
  .venv/bin/python -m pytest
  ```
- 覆盖率：`[tool.coverage.run] source=["app"]`，omit `verify_pipeline.py` 与 `migrations/*`

---

## 6. 部署

### 6.1 生产环境检查清单（安全门禁）

`settings.enforce_production_security()` 在 `APP_ENV != development` 时强制（不达标**拒绝启动**）：

| 检查项 | 要求 | 不达标行为 |
|--------|------|-----------|
| `AUTH_ENABLED` | 必须为 true | 拒绝启动 |
| `SECRET_KEY` | 不能是默认值 | 拒绝启动 |
| `ADMIN_PASSWORD` | 不能是 admin123（或用 `ADMIN_PASSWORD_HASH`） | 拒绝启动 |
| API 文档 | 非 development 自动关闭 `/docs` `/redoc` `/openapi` | — |

生产 `.env` 示例：
```bash
APP_ENV=production
AUTH_ENABLED=true
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_PASSWORD_HASH=$2b$12$...     # bcrypt 哈希
LOGIN_RATE_LIMIT=5/minute
```

### 6.2 Docker 部署

`docker-compose.yml` 仅含 `backend` 服务（前端由 FastAPI StaticFiles 服务）：

```bash
# 先构建前端
cd frontend && npm run build && cd ..
docker-compose up --build
```

挂载与配置：
- `config.yaml` → `/app/config.yaml:ro`
- `data/` / `models/` / `logs/` → `/app/...`（可写）
- `frontend/dist` → `/app/static:ro`
- 环境变量：`PROJECT_ROOT=/app`、`STATIC_DIR=/app/static`、`TZ=Asia/Shanghai`
- 启动命令：`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`
- 资源限制：内存 4g
- 健康检查：`GET /health`，30s 间隔，20s start_period

> ⚠️ `workers=1`：SQLite + APScheduler 单例不宜多 worker；如需多进程需换 DB 与分布式调度。

### 6.3 反向代理配置

`frontend/nginx.conf` 提供生产反向代理模板，要点：
- 前端静态资源由 nginx 直接服务
- `/api/`、`/ws` 反代到后端 8000
- WebSocket 需 `Upgrade`/`Connection` 头透传
- 静态资源 gzip 缓存

### 6.4 健康检查

`GET /health` 返回：
```json
{
  "status": "ok|degraded",
  "timestamp": "...",
  "version": "0.9.0-beta.1",
  "checks": {"database": "ok", "qlib": "ok|not_available"}
}
```

---

## 7. 常见问题

### 7.1 WSL 路径问题

- **现象**：在 Windows 侧用 `\\wsl$\` 编辑后运行报错
- **解决**：所有源码与 `.venv` 在 WSL 内操作；`start.sh` 自动用 `.venv/bin/python`
- **Docker Desktop**：确保 WSL2 后端已启用

### 7.2 QLib 初始化失败

- **现象**：`QLIB_NOT_AVAILABLE`（503），`is_qlib_available()` 返回 False
- **排查**：
  1. 检查 `data/qlib_bin/cn_data/` 是否存在且含 `calendars/day.txt`
  2. `GET /api/v1/quant/data/qlib-status` 查看详细 message
  3. 未装 pyqlib：`.venv/bin/pip install pyqlib`（注意 protobuf<4、setuptools<81）
  4. 数据未同步：`POST /api/v1/quant/data/sync` 或 `POST /api/v1/quant/data/eod-sync`

### 7.3 akshare 接口超时/限流

- **现象**：同步失败，日志 `数据同步失败 (attempt x/3)`
- **原因**：akshare 上游改版或高频访问被限流
- **解决**：
  1. 调大 `config.quant.fetch_interval_seconds`（默认 1.2s）
  2. 调小 `fetch_max_workers`（默认 3）
  3. 切换到 chenditc：`PUT /api/v1/quant/data/data-source?source=chenditc`
  4. 定时任务自带 3 次重试，间隔 10 分钟

### 7.4 数据同步失败排查

| 现象 | 排查 |
|------|------|
| 状态卡 `syncing` | 超过 30 分钟自动标 failed；或调 `GET /quant/data/status` 触发 `_detect_stale_sync` |
| 重复触发 409 | 10 分钟内重复同步返回 `SYNC_IN_PROGRESS`，等待或超时后重试 |
| bin 长度异常 | `GET /quant/data/integrity-check?universe=csi300` |
| 同步历史 | `GET /quant/data/sync-history` 查看版本/耗时/错误 |
| 进度无更新 | `GET /quant/data/sync-progress` 查看实时进度 |

### 7.5 挖掘任务卡 running

- 每 10 分钟 `reap_stale_mining` 自动回收
- 手动查 `GET /api/v1/mining/tasks?status=running`
- LLM 挖掘不限时（依赖内部原子超时 + `llm_hard_limit_seconds` 硬上限，默认 7200s）

### 7.6 SQLite locked

- 已设 `busy_timeout=5000` + WAL 模式
- 若仍频繁 locked，检查是否有外部进程访问 `data/quantlab.db`，或降低并发

---

## 8. 开发常用命令速查

```bash
# 后端
.venv/bin/python -m uvicorn app.main:app --reload    # 启动
.venv/bin/python -m pytest                            # 测试
.venv/bin/python -m pytest --collect-only -q          # 统计用例数
ruff check backend/app && ruff format backend/app     # lint+format
cd backend && python -m alembic upgrade head          # 迁移

# 前端
cd frontend && npm run dev       # 开发
cd frontend && npm run build     # 构建
cd frontend && npm run lint      # 检查

# Docker
docker-compose up --build
```

---

*文档版本：1.0 · 最后更新：2026-07-31*
