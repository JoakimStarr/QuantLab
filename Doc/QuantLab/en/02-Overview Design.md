# Overview Design

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

## 1. System Architecture

### 1.1 Design Patterns

| Pattern | Where | Note |
|---------|-------|------|
| Layered | Global | Presentation → API → Service → Data (SQLAlchemy / qlib bin) |
| Facade | `app/services/quant/*` | Wraps qlib complex API |
| Factory + Strategy | `backtest_engine.py` | qlib/vbt backend selection |
| Observer/Broadcast | `sync_worker.py` + `sync_progress.json` + WebSocket | subprocess progress → main → frontend |
| Thread/Process Pool | `app/core/executor.py` | `io_executor`, `cpu_executor`, `run_io_cpu/run_cpu/run_mixed` |
| Single Writer | batch evaluation | asyncio.Queue + one DB consumer |
| Narrow table (EAV) | `macro_indicator` / `financial_indicator` | extend indicators without DDL |
| Dual-store | market data | qlib bin (factor calc) + PostgreSQL (raw/PIT) |

### 1.2 Architecture Diagram

```
┌────────────────────────────────────────────────────────────┐
│                      Vue 3 Frontend (:3000)                 │
│  Dashboard/Factors/Strategy/Mining/Data/Macro/Compare/Logs  │
│  Pinia + axios (/api) + WebSocket (/ws progress push)       │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP REST + WS (Vite proxy :8000)
┌──────────────────────────▼─────────────────────────────────┐
│                  FastAPI App (:8000)                        │
│  app/main.py (lifespan/middleware/errors/health/SPA)        │
│  app/api/* → ApiResponse {ok,data,error}                    │
│  app/core/* (config/db/auth/executor/scheduler/ratelimit/   │
│               metrics/recovery/websocket)                   │
└──────┬───────────────────────────────┬─────────────────────┘
       │ run_io_cpu/run_cpu             │ async SQLAlchemy (asyncpg)
┌──────▼───────────────┐      ┌────────▼─────────────────────┐
│  Service layer        │      │  PostgreSQL                  │
│  quant/factor/mining/ │      │  factor/strategy/mining_task/│
│  data/ai/task         │      │  backtest_result/...         │
└──────┬───────────────┘      │  stock_daily/etf_daily/       │
       │                      │  macro_indicator/financial... │
┌──────▼───────────────┐      └───────────────────────────────┘
│  sync_worker subprocess│ spawn_sync_worker(kind, ...)
│  (baostock/akshare/    │──────────┐
│   eastmoney crawlers)  │          │
│  data/sync_progress.json bridge   │
└──────┬───────────────┘          │
       │ atomic writes (tmp + os.replace)
┌──────▼──────────────────────────────────────────────────────┐
│  qlib bin (data/qlib_bin/cn_data/)                           │
│  features/{code}/{field}.day.bin (float32)                   │
│  calendars/day.txt + instruments/*.txt                       │
│  19 stock fields + derived factor/tradable/change + macro/fin│
└─────────────────────────────────────────────────────────────┘
```

### 1.3 Key Mechanisms

- **Executor**: qlib reads/eval/backtest are CPU/IO-heavy → `run_io_cpu` dispatches to thread/process pools so the event loop stays free.
- **Sync subprocess**: each sync/repair runs via `spawn_sync_worker` with `start_new_session=True`; main process reads `sync_progress.json`; `sync_lock` flock serializes baostock crawlers.
- **Calendar growth**: after `day.txt` gains a day, all bins NaN-padded via `_pad_bins_to_calendar`; `calendar_shifting_active` blocks backtest/mining during shifts.

## 2. Module Breakdown

| Module | Namespace | Responsibility |
|--------|-----------|----------------|
| API routers | `app/api/` | REST endpoints; ext routers registered before base to avoid path shadowing |
| Core | `app/core/` | config, database, auth, executor, scheduler, ratelimit, metrics, recovery, websocket, middleware, errors |
| Data services | `app/services/data/` | backfill, EOD, validation, repair, full sync, index/ETF sync, macro, fundamental, external market, sync worker |
| Factor services | `app/services/factor/` | library CRUD, expression sandbox, Alpha158 batch eval/backfill, neutralize, orthogonalize, compare |
| Quant services | `app/services/quant/` | qlib init, factor eval, backtest engines (qlib/vbt), portfolio, walk-forward |
| Mining services | `app/services/mining/` | LLM, symbolic, AutoML, text factor |
| AI services | `app/services/ai/` | LLM clients + multi-provider failover |
| Task services | `app/services/task/` | scheduled jobs (decay check, stale reaper) |
| Models | `app/models/` | SQLAlchemy ORM |
| Schemas | `app/schemas/` | Pydantic I/O models |
| Frontend | `frontend/src/` | Vue 3 pages, Pinia stores, axios api wrappers, WS composable, router |
| Migrations | `backend/migrations/` | Alembic additive + defensive (`sa.inspect()`) |

## 3. Class Relationships

```
Base (SQLAlchemy Declarative)
 ├─ StockDaily / EtfDaily / StockBasic / StockIndustry / TradeCalendar
 ├─ MacroIndicator / FinancialIndicator      (narrow tables, unique + PIT)
 ├─ StockIndex                               (index/etf registry, type col)
 ├─ Factor / Strategy / MiningTask / BacktestResult / User
 ├─ StockDataStatus / SyncHistory / TaskResult
 └─ (create_all builds schema; Alembic adds incrementally)

executor.py
 ├─ io_executor: ThreadPoolExecutor
 ├─ cpu_executor: ProcessPoolExecutor
 └─ run_io_cpu(fn, ...) / run_cpu(fn, ...) / run_mixed(...)

sync_worker.py
 └─ spawn_sync_worker(kind, universe, ...) → subprocess
     ├─ backfill/eod/repair/indices/etf/macro/fundamental/external/full
     └─ progress → data/sync_progress.json

backtest_engine.py
 └─ create_backtest_engine(backend) → QlibBacktestEngine | VbtBacktestEngine
```

## 4. Core Flows

1. **sync-full**: frontend trigger → spawn `kind=full` worker → staged A-share backfill → indices → macro → financials → external market; progress pushed via WebSocket; `sync_history` written on completion.
2. **factor evaluate**: validate expression (AST sandbox) → `run_io_cpu` qlib compute → write IC/RankIC/ICIR/turnover/decay/horizon back to `factor`.
3. **strategy backtest**: select engine by `backend` → run via executor with WS progress → persist `backtest_result` (snapshot + nav/metrics/trades).
4. **validate/repair**: cross-store checks → issue summary → repair rebuilds day.txt/bins and rebroadcasts.
5. **mining**: submit engine task (3/min) → `mining_task` lifecycle → candidates screened into `factor` → auto-recovery on restart.

## 5. Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.11 (pinned) | runtime (pyqlib needs ≤3.12) |
| FastAPI + uvicorn | - | async REST |
| SQLAlchemy 2.0 + asyncpg / psycopg | - | async ORM + drivers |
| qlib (pyqlib) | - | factor computation, backtest |
| baostock / akshare | - | market/fundamental data |
| slowapi | - | rate limiting (mining 3/min) |
| apscheduler | - | scheduled jobs |
| fastapi-users | - | auth base |
| prometheus-client | - | /metrics |
| Vue 3 + Element Plus + Pinia + vue-echarts | - | frontend + charts |
| Vite | - | build + proxy |
| PostgreSQL | 16 (CI) / 18 (local) | business data |
