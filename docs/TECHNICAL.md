# QuantLab 技术文档

> 版本：2.0.0　最后更新：2026-07-30
> 配套仓库：[JoakimStarr/QuantLab](https://github.com/JoakimStarr/QuantLab)

本文档描述 QuantLab 的架构设计、核心模块、执行模型与运维要点，供开发者快速上手与二次开发。

---

## 1. 系统架构

### 1.1 整体分层

```
┌──────────────────────────────────────────────────────────┐
│  前端  Vue 3 + Element Plus + ECharts + Pinia            │
│  ├─ Dashboard / FactorLibrary / Strategy / Mining        │
│  ├─ DataStatus / BacktestCompare / FactorCompare         │
│  └─ WebSocket 实时推送（任务进度/日志）                    │
└────────────────────────┬─────────────────────────────────┘
                         │ HTTP REST + WS
┌────────────────────────▼─────────────────────────────────┐
│  后端  FastAPI (asyncio) + SQLAlchemy + SQLite           │
│  ├─ api/        路由层（auth/quant/factor/strategy/mining）│
│  ├─ core/       配置/数据库/鉴权/限流/调度/执行器/恢复     │
│  ├─ models/     ORM（Factor/Strategy/BacktestResult/...）  │
│  └─ services/   业务核心                                   │
│     ├─ quant/   qlib 封装（数据适配/因子评价/回测/组合）    │
│     ├─ factor/  因子库（表达式沙箱/CRUD/Alpha158/中性化）  │
│     ├─ strategy/ 策略管理 + 参数寻优 + walk-forward        │
│     ├─ mining/  AI 因子挖掘（LLM/符号回归/文本/AutoML）    │
│     ├─ ai/      LLM 客户端 + 多 provider 路由             │
│     └─ data/    AKShare + qlib bin 数据同步                │
└────────────────────────┬─────────────────────────────────┘
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
   pyqlib 引擎      AKShare/qlib bin    LLM Providers
   (CPU 进程池)      (IO 线程池)        (opencodezen/GLM/SiliconFlow)
```

### 1.2 进程模型

- **Web 进程**：`uvicorn` 单进程异步，处理 HTTP/WS。
- **IO 线程池**（`core/executor.py::get_io_executor`）：同步 IO（AKShare/qlib 数据读取），默认 8 线程。
- **CPU 进程池**（`core/executor.py::get_cpu_executor`）：纯函数 CPU 密集任务（`evaluate_factor` / `time_series_cv_eval`），默认 `cpu_count//2` 进程，绕过 GIL 真并行。
- **后台任务**：`BackgroundTasks` + `asyncio.create_task`，挖掘/回测在事件循环内调度，状态落库。

> 设计要点：CPU 密集任务必须走进程池，否则会占满默认线程池拖垮事件循环（含健康检查）。

---

## 2. 核心模块

### 2.1 配置（`core/config.py`）

`Settings` 单例，启动时从 `config.yaml` + 环境变量加载，三层优先级：环境变量 > config.yaml > 默认值。

**安全闸门**：
- `validate_security()`：启动告警（不阻断），列出弱配置。
- `enforce_production_security()`：生产环境（`APP_ENV != development`）强制阻断——默认 `SECRET_KEY`/`ADMIN_PASSWORD`、未开鉴权时直接 `raise RuntimeError` 拒绝启动；同时关闭 `/docs`、`/redoc`、`/openapi.json`。

### 2.2 执行器（`core/executor.py`）

| 函数 | 池类型 | 适用场景 | 约束 |
|---|---|---|---|
| `run_io(func, *args)` | ThreadPool | AKShare/qlib 同步数据读取 | 无 |
| `run_cpu(func, *args)` | ProcessPool | `evaluate_factor`/CV 训练等纯函数 | func 须模块级可 pickle，禁闭包/lambda |

worker 数由 `config.task.cpu_workers` / `io_workers` 配置。应用关闭时 `shutdown_executors()` 清理。

### 2.3 鉴权（`core/auth.py` + `api/auth.py`）

- JWT（`HS256`），`SECRET_KEY` 签名，默认 7 天有效。
- 口令：`ADMIN_PASSWORD` 明文或 `ADMIN_PASSWORD_HASH`（bcrypt 哈希，优先）。
- 依赖注入：`require_user`（必鉴权）/ `optional_user`（可选鉴权，本地开发关鉴权时放行）。
- 鉴权开关：`AUTH_ENABLED`，未设时按 `APP_ENV` 判定（development=关，其他=开）。
- token 传递：`Authorization: Bearer <token>` 或 cookie（`__Host-token`），后端 `_extract_payload` 均支持。

**登录限流**：`slowapi` 5 次/分钟（`core/ratelimit.py`）。挖掘提交端点 3 次/分钟。

### 2.4 数据库与迁移

- ORM：SQLAlchemy 2.0 async + aiosqlite，单文件 `data/quantlab.db`。
- 建表：`init_db()` 用 `Base.metadata.create_all` 建新表。
- 列变更：Alembic 管理，`init_db()` 后自动 `alembic upgrade head`（子线程执行，失败仅告警不阻断）。
- 迁移文件：`backend/migrations/versions/`，新增字段时在 revision 的 `upgrade()` 用 `_add_column_if_not_exists`（PRAGMA 检测 + ALTER）。

### 2.5 任务恢复（`core/recovery.py`）

| 函数 | 作用 |
|---|---|
| `recover_stale_sync` | 标记卡死的同步任务为 failed（超时 reaper） |
| `recover_stale_mining` | 回收僵尸挖掘任务（running 超时） |
| `rerun_pending_mining` | **重启后重跑** pending/running 的挖掘任务（近 3 天），running 重置为 pending 后重新派发 |

应用 `lifespan` 启动时依次调用，保证崩溃后任务不丢失。

### 2.6 量化引擎（`services/quant/`）

- `qlib_init.py`：qlib 初始化（线程锁保护，可经 `run_in_executor` 并发调用）。
- `data_adapter.py`：qlib 数据读取封装。
- `factor_eval.py`：因子评价（IC/RankIC/ICIR/换手/衰减）。`compute_decay` 一次查询 `$close` 后本地 shift 计算各 lag 前向收益，避免 N 次 qlib IO。
- `backtest_engine.py`：top-k dropout 选股回测，支持涨跌停/停牌过滤、日/周/月调仓、买卖手续费 + 可选滑点（`slippage_bps`）。

### 2.7 因子挖掘（`services/mining/`）

四种挖掘方式，均入库前做 walk-forward 样本外 IC 校验 + AST 规范化去重：

| 类型 | 入口 | 引擎 | 特点 |
|---|---|---|---|
| LLM | `llm_factor.py::mine_with_llm_iterative` | 多 provider 路由 | 迭代轮次反馈改进，`n_rounds>1` 启用 |
| 符号回归 | `symbolic.py::mine_with_symbolic` | gplearn 遗传编程 | 时序分割防泄露，Pareto 前沿取 top |
| 文本因子 | `text_factor.py::mine_with_text` | 新闻情感 + LLM | 指定股票代码或 universe 前 30 |
| AutoML | `automl.py::mine_with_automl` | LightGBM/Ridge | 因子组合，时序 CV 评估泛化性 |

**LLM Provider 路由**（`services/ai/provider_router.py`）：单例，顺序 fallback（primary→fallback→tertiary），`ProviderRouter()` 全局复用 `AsyncOpenAI` 连接池。

### 2.8 表达式沙箱（`services/factor/expression.py`）

AST 解析因子表达式，**防 look-ahead bias**：
- 白名单算子：`Ref/Mean/Std/Max/Min/Sum/Rank/Corr/Cov/Delta/Slope/Resi/WMA/EMA`。
- 禁止 `Ref` 正向偏移（未来数据）、禁止赋值/导入/调用非白名单函数。
- 入库前 AST 规范化去重（避免同义表达式重复入库）。

---

## 3. API 概览

所有 API 前缀 `/api/v1`，响应统一 `{ok, data, error}`。

| 模块 | 代表端点 | 说明 |
|---|---|---|
| auth | `POST /auth/login`、`GET /auth/status`、`GET /auth/ai-status` | 登录/状态/可用 provider 探测 |
| quant_data | `GET /quant/data/qlib-status`、`POST /quant/data/sync` | qlib 可用性/数据同步 |
| factor | `GET /factors`、`POST /factors/{id}/evaluate` | 因子 CRUD + 评价 |
| factor_ext | `POST /factors/compare`、`GET /factors/{id}/decay`、`POST /factors/seed-alpha158` | 因子对比/衰减/Alpha158/中性化 |
| strategy | `POST /strategies/{id}/backtest`、`GET /strategies/{id}/backtest-results` | 策略 + 回测 |
| strategy_ext | `POST /strategies/{id}/param-sweep`、`POST /strategies/{id}/walk-forward` | 参数寻优/walk-forward |
| mining | `POST /mining/llm`、`/symbolic`、`/automl`、`/text` | AI 因子挖掘（3/min 限流） |
| mining_ext | `GET /mining/templates`、`POST /mining/templates/{key}/run` | 挖掘模板 |
| market | 行情数据 | K线/盘口 |

WebSocket：`/ws?token=<jwt>`，推送任务进度与日志。

---

## 4. 部署

### 4.1 开发环境

```bash
pip install -r requirements-dev.txt   # 含测试工具
cd frontend && npm install && cd ..
cp .env.example .env                  # 填 API Key
./start.sh dev                        # 后端 :8000 前端 :3000
```

### 4.2 Docker

```bash
docker compose up -d --build          # 见 docker-compose.yml
```

生产部署前**必须**设置：`APP_ENV=production`、`AUTH_ENABLED=true`、`SECRET_KEY=<强随机串>`、`ADMIN_PASSWORD=<强口令>` 或 `ADMIN_PASSWORD_HASH=<bcrypt>`，否则启动闸门拒绝启动。

### 4.3 CORS

默认 `http://localhost:3000`。分离部署时设环境变量 `CORS_ORIGINS=https://your-domain,https://app.your-domain`（逗号分隔）或 `config.api.cors_origins`。

---

## 5. 测试与质量

- 后端：`pytest backend/tests/`，112 项，覆盖 auth/expression/factor_eval/backtest_engine/symbolic。
- 前端：`npm run build`（暂无单元测试，建议补 vitest）。
- CI：`.github/workflows/ci.yml`，ruff + pytest + 前端 build。
- 覆盖率门槛：后端 ≥ 40%（`pyproject.toml` 配置）。

---

## 6. 运维要点

1. **CPU 任务隔离**：挖掘/评价走进程池，`config.task.cpu_workers` 建议设为 `cpu_count//2`，避免与 Web 进程争抢。
2. **任务持久化**：进程崩溃后重启会自动重跑近 3 天的 pending/running 挖掘任务；如需禁用，清空对应 MiningTask 记录。
3. **Alembic 迁移**：新增模型字段后，在 `migrations/versions/` 新建 revision 并用 `_add_column_if_not_exists` 声明，启动时自动 upgrade。
4. **日志**：`logs/` 下 `app.log`（滚动）/`api.jsonl`/`audit.jsonl`/`perf.jsonl`/`error.log`，结构化 JSON 便于采集。
5. **数据备份**：SQLite 单文件 `data/quantlab.db`，定期 `sqlite3 .backup` 备份。

---

## 7. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| CPU 密集任务 | ProcessPoolExecutor | GIL 下线程无加速，且会拖垮事件循环 |
| 任务持久化 | DB 状态机 + 重启重跑 | 无需引入 Redis/Celery，契合单机研究平台规模 |
| 迁移策略 | create_all + Alembic ALTER | SQLite 单文件，alembic 全量管理成本高于收益 |
| LLM 路由 | 顺序 fallback 单例 | 免费模型超时长，并发竞速浪费预算；单例复用连接池 |
| 表达式安全 | AST 白名单 | 防 look-ahead bias 与代码注入，比正则更严格 |
| token 存储 | localStorage | 后端已支持 cookie，前端暂用 localStorage，后续可平滑切换 |

---

如有疑问，提 [GitHub Issue](https://github.com/JoakimStarr/QuantLab/issues)。
