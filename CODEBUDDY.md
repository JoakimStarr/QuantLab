# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## Project overview

QuantLab is a quantitative strategy research platform: FastAPI (async) backend + Vue 3 frontend + qlib for factor evaluation, backtesting, and AI-driven factor mining. It stores market data as qlib `.day.bin` (float32) files and business data (factors, strategies, mining tasks, backtests) in PostgreSQL.

- Backend: `backend/app` (FastAPI, SQLAlchemy 2.0 async, Pydantic)
- Frontend: `frontend/src` (Vue 3 `<script setup>`, Element Plus, Pinia, vue-echarts)
- Config: `config.yaml` (runtime tuning) + `.env` (secrets, DB connection, API keys)

## Commands

Python is pinned to 3.11 (`pyqlib` does not support 3.13). The venv lives at `.venv/`; the backend must be run with `.venv/bin/python`.

```bash
# One-time environment bootstrap (venv + deps + data dirs + .env)
./setup.sh

# Start dev services (backend :8000 with --reload, frontend :3000 via Vite)
./start.sh dev        # 应用日志写 logs/quantlab.log + logs/error.log，worker 写 logs/sync.log
                      # （结构化 JSON，前端"日志管理"页可视化查看；前端页面日志在浏览器 DevTools）

# Backend alone (from repo root)
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000  # cwd=backend

# Frontend alone
cd frontend && npm run dev

# Manual data operations (no auto-sync; all run in an independent worker subprocess)
curl -X POST 'http://localhost:8000/api/v1/quant/data/sync-full?years=5'        # 一键全同步：A股回填→指数→宏观→财报→外盘
curl -X POST 'http://localhost:8000/api/v1/quant/data/eod-sync?source=baostock&days=5'   # 增量 EOD（baostock/akshare）
curl -X POST 'http://localhost:8000/api/v1/quant/data/fundamental/sync?broadcast=true'  # 财报拉取(+广播到 bin)
curl -X POST 'http://localhost:8000/api/v1/quant/data/sync-external-market'     # 外盘隔夜情绪因子
curl 'http://localhost:8000/api/v1/quant/data/validate?universe=all'            # 全市场数据校验
curl -X POST 'http://localhost:8000/api/v1/quant/data/repair' -H 'Content-Type: application/json' -d '{"universe":"all","include_baostock":false}'
```

### Tests

Tests require `DATABASE_URL` to be set for DB-backed tests; those tests auto-skip when it's absent (see `backend/tests/conftest.py`). The local Postgres role/db is `quantlab`/`quantlab`. Unit tests that mock the DB/qlib run without it.

```bash
# All tests (from repo root; pytest.ini sets pythonpath=backend, asyncio_mode=auto)
DATABASE_URL=postgresql+asyncpg://quantlab:quantlab@localhost:5432/quantlab .venv/bin/python -m pytest

# Single file / single test
.venv/bin/python -m pytest backend/tests/test_alpha158.py
.venv/bin/python -m pytest backend/tests/test_alpha158.py::TestBackfillAlpha158Metrics::test_backfill_with_ids_no_match
```

`backend/tests/conftest.py` injects a fake `qlib` module so mock-based tests run without qlib installed; real qlib computation tests require the venv (which has qlib).

**Test-isolation warning**: conftest's table-TRUNCATE/drop-all only runs when the DB name contains `"test"` (protects real dev DBs). If `DATABASE_URL` points at the real `quantlab` DB, DB-writing tests (e.g., `test_eod_incremental.py` calling `incremental_sync_eod` → `_refresh_status_after_eod`) will **pollute** `stock_data_status`/`sync_history`. Such tests must mock `_refresh_status_after_eod` (an `AsyncMock`), and after running the suite against the real DB, manually restore/clean those rows. Prefer a `quantlab_test` DB for full-suite runs.

Note: `test_macro_api.py::test_macro_upsert_idempotent` is flaky-by-design — it writes a fixed `(PMI, 2026-07-01, pmi)` row and asserts `n1 == 1`, which fails on the second run because the row already exists in the DB. Deselect it (`--deselect ...`) or delete the row before running.

### Lint / format / build

Dev tools (ruff, mypy, bandit, pytest-cov) are declared in `requirements-dev.txt` and are **not installed in the current venv** — install them with `.venv/bin/pip install -r requirements-dev.txt` before running the backend linters.

```bash
cd backend && ../.venv/bin/ruff check app/          # ruff, line-length=120 (config in pyproject.toml)
cd backend && ../.venv/bin/ruff format app/         # quote-style="double"

cd frontend && npm run lint                          # ESLint 9 + eslint-plugin-vue
cd frontend && npm run check:vue                     # SFC parse check
cd frontend && npm run format                        # Prettier
cd frontend && npm run build                         # vite build → dist/
```

CI (`.github/workflows/ci.yml`) runs: `flake8 app/ --max-line-length=120`, `npm run lint`, `npm run check:vue`, `npm run build`, and pytest with a Postgres 16 service container.

### Database (Alembic)

```bash
cd backend
POSTGRES_USER=quantlab POSTGRES_PASSWORD=quantlab POSTGRES_DB=quantlab \
  ../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m alembic revision --autogenerate -m "description"
```

Important: the baseline migration `23fc4c667c2f` is an **empty** `upgrade` — tables are created by `Base.metadata.create_all`, and migrations only add columns/indexes defensively. On a fresh DB you must run `create_all` first (the app's `init_db()` does this), then `alembic upgrade head`. `migrations/versions/b5e1f7g8h9i0` was rewritten to use `sa.inspect()` for the existence check — do not "fix" it back to raw-string `bind.execute(...)` (that raises `ObjectNotExecutableError` under SQLAlchemy 2.0 + psycopg).

### One-off scripts

- `backend/scripts/migrate_sqlite_to_pg.py` — migrated the old SQLite DB (`data/quantlab.db`) to Postgres. Idempotent (ON CONFLICT DO NOTHING), column-intersection copy.
- `backend/scripts/rebuild_automl_bundles.py` — retrains missing AutoML model bundles (`data/models/automl/{task_id}.pkl`) from each task's original params, then removes the duplicate factor `mine_with_automl` creates and restores the original factor's metrics/`result_factor_ids`.
- `backend/scripts/seed_indices.py` — registers the index dirs under `features/` (sh000001/sz399001/...) into the `stock_index` table (idempotent). Run once after a fresh backfill; index sync also auto-registers.

## Architecture

### Backend

- **App factory** (`backend/app/main.py`): FastAPI app with unified error handlers (all responses wrapped in `ApiResponse {ok, data, error}`, see `app/schemas/common.py` and `app/core/errors.py`), Prometheus `/metrics`, and a `/health` endpoint.
- **Lifespan sequence** (`main.py`): `setup_logging` → security checks → `init_db()` (create_all + alembic upgrade head in a subprocess) → recover stale sync/mining tasks → rerun pending mining → `start_scheduler()`. **Do not add heavy background work here** — startup must stay fast and `/health` must respond immediately; heavy compute (e.g., factor metric backfill) runs only on manual API trigger.
- **DB connection** (`app/core/database.py`): **PostgreSQL only** (asyncpg for the app, psycopg for alembic) — there is no SQLite fallback despite stale docs. URL resolution order: `DATABASE_URL` env → `POSTGRES_USER/PASSWORD/DB/HOST/PORT` env. `.env` is loaded by `app/core/config.py` (`model_post_init` → `load_dotenv`), which is imported before `database.py`.
- **API routing** (`app/api/router.py`): routers registered under `/api/v1`. **`*_ext` routers are registered BEFORE base routers** to avoid path shadowing (e.g., `/{strategy_id}` catching `/backtest-statuses`). Auth via `require_user` dependency (no-op when `AUTH_ENABLED=false`). Notable routers: `market.py` (dashboard 行情：`/market/overview`、`/market/kline/{code}`、`/market/indices`; **读 qlib `D.features` 时过滤 close 为 NaN 的日历日**——未发布日不该返回)，`strategy_rule.py`（策略库 `/strategy-library/templates` + `/strategy-library/backtest`，不持久化、运行即返回），`data_ext.py`（数据管理扩展：sync-progress/preview/stocks-search/sync-full 等）。
- **Executors** (`app/core/executor.py`): `io_executor` (ThreadPool), `cpu_executor` (ProcessPool), helpers `run_cpu`, `run_io_cpu`, `run_mixed`. Factor evaluation, backtesting, and qlib data reads are CPU/IO-heavy — **always** dispatch them through these helpers (`await run_io_cpu(...)`) and never run blocking qlib calls directly in the event loop (it freezes `/health` and other requests).
- **Scheduler** (`app/core/scheduler.py` + `app/services/task/update_service.py`): APScheduler jobs — 18:05 factor decay check + every-10-min stale mining reaper. **There is no automatic data sync**; data is synced only via the manual API trigger (baostock backfill, see below).
- **Logging** (`app/core/logging_config.py`): 统一 structlog JSON，web 进程与 sync/ming worker 共用 `setup_logging()`。3 个文件：`quantlab.log`（web 全量 INFO+）、`error.log`（web WARNING+，备份保留 15 天）、`sync.log`（全部 sync worker，行内 `worker_kind` 字段区分任务类型）、`mining.log`（挖掘 worker，`worker_kind=mining:{type}`）。`LockedRotatingFileHandler`（fcntl）保证 worker 跨进程写日志安全。**消噪**：`uvicorn.access`→WARNING、`asgi_correlation_id`→ERROR（曾淹没 error.log）、`apscheduler`→WARNING；uvicorn 启动日志已落盘。`PUT /logs/level` 运行时调级（DEBUG 排查：切 DEBUG→复现→看 quantlab.log→切回 INFO）。审计事件走统一管道（`logger="audit"`，登录/登出/挖掘/回测提交）。清理模式只匹配数字后缀备份（`quantlab.log.[0-9]*` 等），不会误删 `.lock` 锁文件。

### Service layers (`backend/app/services/`)

- `quant/` — qlib integration: `factor_eval.py` (IC/RankIC/ICIR/turnover evaluation, handles `AutoML(...)` expressions via trained bundles), `backtest_engine.py` / `qlib_backtest.py` / `vbt_backtest.py` (backtest backends), `portfolio*.py`, `walk_forward.py`, `qlib_init.py` (provider_uri init), `rule_backtest.py` (策略库 v1 规则型策略：布林带/双均线/RSI/均线排列/MACD/动量/配对，信号→vbt.Portfolio.from_signals；**交易方向取自 `orders.records_readable` 的 `Side` 列**，Size 恒为正，别用 Size 正负判断买卖). `monte_carlo.py` — 蒙特卡罗分析：① 回测指标 bootstrap 置信区间（`arch` StationaryBootstrap 重采样日收益 → Sharpe/回撤/胜率等 7 指标分布与 90% CI；端点 `POST /strategies/backtest-results/{id}/monte-carlo`，从 nav_curve 重建收益实时算、进程内 LRU 缓存、不改库）；② 因子 IC 置换检验（`permutation_ic_test` 逐日截面打乱因子值构建 IC 零分布 → 非参数 p-value，接入 `deep_analyze_factor` summary 的 `perm_pvalue`，深度分析页展示，不拦截挖掘）。
- `factor/` — factor library: `library.py` (CRUD), `expression.py` (AST sandbox, rejects exec/eval/import and negative `Ref`), `alpha158.py` (seed/import + batch evaluate + backfill metrics; batch uses an asyncio.Queue + single DB writer to avoid connection-pool contention), `neutralize.py`, `orthogonalize.py`, `factor_compare.py`.
- `mining/` — factor mining: LLM (`llm_factor.py`), symbolic regression (`symbolic.py`), AutoML (`automl.py`), text factors (`text_factor.py`), **`mining_worker.py`** — 挖掘任务跑在**独立子进程**（`spawn_mining_worker(task_id, type, params)`，`start_new_session=True`，与 sync_worker 同款设计）：不占 web 事件循环、uvicorn --reload 重启不杀任务。worker 通过 `data/mining_pids/{task_id}.pid` 暴露存活状态（`is_mining_worker_alive`），启动恢复 `recover_stale_mining` 只把 **running 且 worker 已死** 的任务标 failed（存活跳过），pending 留给 `rerun_pending_mining` 重跑——两者不抵消。并发上限由 `task.max_concurrent` 控制，提交前 DB 计数 `running+pending`（`_ensure_mining_capacity`），超限返回 409。
- `data/` — data acquisition & integrity. **`sync_worker.py` is the execution backbone**: every sync/repair runs as an independent subprocess (`spawn_sync_worker(kind, universe, ...)`, `start_new_session=True`, one process per `kind`) that reports progress via `data/sync_progress.json` (web process reads it; `sync_progress.py` + `sync_lock.py` provide a flock "crawl lock" that **serializes all baostock crawlers** — backfill/eod/indices/repair/full; akshare/eastmoney jobs skip it). Kinds: `backfill` (A股 baostock), `eod` (incremental, baostock/akshare via `source`), `repair` (一键补齐), `indices`, `fundamental`, `macro` (宏观同步, eastmoney/akshare), `full` (一键全同步, see below). Key modules:
  - `baostock_backfill.py` — primary A股 sync (triggered via `POST /quant/data/sync-full` stage 1 or repair's `include_baostock`; manual only): newest→oldest, one `query_daily_history_k_AStock` per trading day (whole market), writes qlib bin + PG `stock_daily`/`stock_basic`/`stock_industry`/`trade_calendar`, builds `instruments/*.txt`. Idempotent (`ON CONFLICT DO NOTHING`). `_build_out_df` outputs `factor=1.0` (prices stored qfq-adjusted). **`_rebuild_dynamic_instruments` 拉指数成分失败返回空集时不会覆盖原 csi300.txt/csi500.txt**（否则默认 universe 的挖掘/回测读到空池"无数据"）。
  - `eod_incremental.py` — bin read/write/merge helpers (`_sync_stock_bin`, `_write_calendar`, `_compute_tradable`, **`_pad_bins_to_calendar`**) + `incremental_sync_eod` (EOD for recent days).
  - `validation.py` — cross-store validation (`GET /quant/data/validate`): bin field/length checks, day.txt↔stock_daily↔trade_calendar alignment, DB↔bin coverage, macro/fin field sampling. **Index-aware**: skips `stock_index` codes (indices only have OHLCV). The NaN-ratio "corrupt" heuristic is measured against each stock's own listed range (stock_daily min/max), so recent IPOs aren't false-flagged.
  - `repair.py` — 一键补齐 (`POST /quant/data/repair`): rebuilds day.txt + target stock bins from PG, rebuilds instruments, rebroadcasts macro/fin to bins; only pulls baostock if `include_baostock=true` and PG is missing trading days. Logs (and reports `skipped` for) codes with no `stock_daily` rows instead of silently dropping them.
  - `full_sync.py` — `kind=full` orchestration: A股回填 → 指数 → ETF → **宏观/财报/外盘并行**(`asyncio.create_task`，三者不连 baostock 可并发；通过 `progress_cb` 上报、由 full_sync 统一管理进度文件避免竞态), staged progress; re-sets `worker_pid` after each stage (its `finish+clear` would otherwise drop it, creating a zombie-progress risk).
  - `index_sync.py` + `index_registry.py` + `stock_index` table — indices are a **distinct instrument class**: akshare/baostock index OHLCV only (no 19 stock fields, no stock_daily/financials). Validation/repair look up `stock_index` to exclude them; sync auto-registers; `seed_indices.py` backfills existing dirs.
  - `macro_sync.py` — eastmoney/akshare macro indicators → PG `macro_indicator` narrow table → broadcast to bins (`$pmi`, `$cpi`, `$shibor_*`, ...).
  - `fundamental_sync.py` — akshare per-stock financial abstracts → PG `financial_indicator` narrow table (`code, report_date, field_name, value, available_date`) → PIT forward-fill broadcast (`$roe`, `$netprofit_yoy`, ...). **Incremental skip is report-period aware** (`expected_latest_report_date()` + `_load_fetched_codes`): a code is "already up to date" only if it has ≥ half of `FIN_FIELD_NAMES` **and** its latest `report_date` covers the expected disclosure period (A股法定截止：一季报4/30、中报8/31、三季报10/31、年报次年4/30). This way财报同步只在新的披露窗口到来时重跑，不随每次全同步拉全市场。
  - `external_market.py` — overnight foreign-market factors (`$us_sp500_ret`, `$us_nasdaq_ret`, ...) broadcast to bins.
  - `etf_sync.py` — ETF 同步（`/quant/data/sync-etf`）：baostock `query_daily_history_k_ETF` 按日全市场增量，写 `etf_daily` 窄表 + `features/{code}/` bin（OHLCV 子集，无股票字段），重建 `instruments/etf_all.txt` 全量池，注册 `stock_index`（type='etf'）；另有腾讯 `web.ifzq.gtimg.cn` qfq 对齐回填（`etf_tencent_done.json` 断点续跑，0.4s 限速防风控）。**写 bin 必须 `overwrite=True`**（候选日期都来自 day.txt，False 会导致 bin 一行不写、只落 etf_daily）。
  - `baostock_client.py` — login singleton; constraint: ≤50k requests/day, **no concurrent connections** (serial only). `akshare_client.py` is a supplementary source.
- `ai/` — LLM clients + multi-provider failover.
- `task/` — scheduled job implementations.

### Data storage model

- **qlib bin** (`data/qlib_bin/cn_data/`): float32 per-field files `features/{code_lower}/{field}.day.bin`, plus `calendars/day.txt` (the master time axis, alignment via start_index) and `instruments/{pool}.txt` (`all`/`csiall`/`csi300`/`csi500`/`etf_all`). Stock fields (19): OHLCV + `preclose`/`volume`/`amount`/`turn`/`tradestatus`/`pct_chg`/`is_st`/`pe_ttm`/`pb_mrq`/`ps_ttm`/`pcf_ncf_ttm`/`adjustflag` (baostock subset) + derived `change` (fractional daily return) + `tradable` (limit-up/down + ST 5% mask) + **`factor` (uniform 1.0 — prices are stored qfq-adjusted; qlib needs `$factor` to treat them as adjusted)**. Indices (`sh000001`/`sz399001`/...) and ETFs (`sh510300`/...) live in the same `features/` tree but only carry OHLCV (+ macro/fin broadcast fields). **Calendar-growth rule**: every bin must be exactly `4 + 4×len(day.txt)` bytes. When a new trading day is added (e.g., today's data first arrives), stocks with no data that day (delisted/long-suspended) keep the old length and flag "长度异常" — the writers call `_pad_bins_to_calendar` (NaN-pad) after extending `day.txt` to keep everything aligned. **Bin writes are atomic** (temp + `os.replace`), and backtest/mine are only blocked during calendar-shifting syncs (`calendar_shifting_active`), not EOD/ETF syncs.
- **PostgreSQL** — business tables (`factor`, `strategy`, `mining_task`, `backtest_result`, `user`, `task_result`) + sync metadata (`stock_data_status`, `sync_history`) + market data: `stock_daily` (all baostock daily fields), `etf_daily` (ETF narrow OHLCV), `stock_basic`, `stock_industry`, `trade_calendar`, `financial_indicator` (narrow: code/report_date/field_name/value/available_date), `macro_indicator` (narrow: indicator/report_date/field_name/value), `stock_index` (registered indices **and** ETFs, `type` column 'index'/'etf'). `stock_data_status.latest_date` = **实际落库 `stock_daily` 的最大交易日**（`MAX(trade_date)`），不是 day.txt 末日——day.txt 可能含"今天"（数据未发布），latest_date 应显示最近有数据的日期，前端据此显示"今日数据未发布"。
- The dual-store split: qlib bin holds the daily fields factors reference as `$field`; PG holds the full raw baostock data and anything needing PIT/version semantics.

### Frontend (`frontend/src`)

- `views/quant/` — business pages: `Dashboard.vue`, `FactorLibrary.vue` (expression cells truncate single-line with ellipsis; "补算指标" button in the filter toolbar applies to any selected factor, not just alpha158), `Strategy.vue`, `Mining.vue`, `DataStatus.vue` (data management: "开始同步" submits the full sync chain, plus EOD/指数/财报/外盘 buttons, data validation + 一键补齐 dialogs, an index table from `stock_index`, and a sync-statistics panel; 同步监控/系统监控 pages were removed), `FactorCompare.vue`, `BacktestCompare.vue`, etc.
- `stores/` (Pinia) — `factor.js` holds a 5-min-cached factor list; invalidate after mutations. `api/` — axios wrappers per domain. `composables/` — `useWebSocket`.
- Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`.

## Conventions and gotchas

- **Response envelope**: every business API returns `ApiResponse`; error codes include `VALIDATION_ERROR`, `NOT_FOUND`, `QLIB_NOT_AVAILABLE` (503 when qlib data is not synced), `SYNC_IN_PROGRESS` (409), `AUTH_FAILED`.
- **Concurrency**: keep the event loop free; dispatch qlib/CPU work via `executor.run_io_cpu`/`run_cpu`. Don't add startup background jobs that evaluate factors or load large datasets.
- **Alembic migrations**: always additive and defensive (check existence via `sa.inspect()` before alter), since `create_all` already builds the current model schema on fresh DBs. Don't modify applied migrations.
- **Async sessions**: one session per operation; commits must be short. The alpha158 batch evaluator serializes DB writes through a single queue consumer to avoid pool exhaustion.
- **Data sync**: manual only — `POST /quant/data/sync-full?years=N` (一键全同步 chain: A股回填 → 指数 → ETF → 宏观/财报/外盘并行) and `POST /quant/data/repair` (补齐, may include baostock). Backfill iterates trading days newest → oldest, one `query_daily_history_k_AStock` per day (whole market), then writes qlib bin + PG `stock_daily`. PG inserts are idempotent (`ON CONFLICT DO NOTHING`), so re-running with more years only fills gaps. Respect baostock's limits: ≤50k requests/day, no concurrent connections (serial only; the `sync_lock` flock serializes baostock crawlers). **Long syncs run as independent worker subprocesses** (`sync_worker.py`) — never run baostock/akshare jobs as FastAPI `BackgroundTasks` in-process (uvicorn `--reload` waits forever on them); progress is bridged through `data/sync_progress.json`.
- **Indices are not stocks**: `features/` also holds index dirs (sh000001/sz399001/...). They only have OHLCV, no `stock_daily`/financials, and must be excluded from validation/repair (via the `stock_index` table). Don't treat every `features/*` dir as a stock.
- **`factor` bin is a constant 1.0** (derived, since prices are stored qfq-adjusted); it is not a baostock field. When `BIN_FIELDS` changes, existing bins must be regenerated (e.g., a fresh backfill/repair) or the missing-field check flags every stock.
- **Calendar growth must pad all bins**: after `day.txt` gains a trading day, call `_pad_bins_to_calendar` so delisted/suspended stocks' bins are NaN-padded to the new length (see `eod_incremental.py`); otherwise validation reports 长度异常.
- **Backtest default date range = recent 2 years → latest data date**: `Strategy.vue`/`StrategyLibrary.vue` 打开回测弹窗时异步调 `/quant/data/status` 取最大 `latest_date`，默认区间 `[latest-2y, latest]`（取不到回退今天）。不要硬编码 `2020-01-01` 或用"今天"当终点（今天数据未发布时回测尾端全是 NaN）。
- **今日数据未发布是常态**: baostock 当日 K 线要收盘后（约 18:00）才发布。`/market/*` 和 `/preview` 读 qlib bin 时必须过滤 `close` 为 NaN 的日历日，否则前端把 NaN 当最新行情/指标。前端 `dataNotToday` 计算属性据此显示"今日数据未发布"提示。
- **Docs staleness**: `docs/DATA_LAYER.md` and parts of `README.md`/`DEVELOPMENT.md` reference removed components (SQLite, `capital_flow_sync.py`, chenditc/akshare-default data source, 同步监控 page, "factor not stored"). Trust the code over those docs.
- **Legacy SQLite read in AutoML loader**: `factor_eval.py` `_resolve_task_id_from_factor_ids()` reads the stale `data/quantlab.db` via `sqlite3` to map old-format `AutoML(method, fid1, ...)` expressions to a task_id (leftover from the SQLite→PG migration). It only works while that file exists; a follow-up should re-point it at Postgres.
- **Environment**: `.env` is required (AI provider keys, `POSTGRES_*`); `SECRET_KEY` and `ADMIN_PASSWORD` defaults are dev-only — `APP_ENV=production` with defaults blocks startup (security gate in `app/core/config.py`).
