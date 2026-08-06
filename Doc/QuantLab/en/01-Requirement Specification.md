# Requirement Specification

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

## 1. Overview

QuantLab is an end-to-end quantitative research platform covering the full pipeline: **Data → Factor → Strategy → Backtest → Mining → Review**.

- **Data layer**: pulls A-share daily K-line, indices, ETFs, macro indicators and financial reports from baostock/akshare/eastmoney; dual-writes to qlib `.day.bin` binaries and PostgreSQL. Supports full backfill, incremental EOD, one-click repair, and data validation (manual-only operations).
- **Factor layer**: qlib-expression factor library with AST sandbox validation; batch evaluation (IC/RankIC/ICIR/turnover/multi-horizon/orthogonal residual); built-in Alpha158 import and metric backfill.
- **Strategy layer**: multi-factor portfolios (equal-weight/IC-weighted/LightGBM/Stacking), TopK selection, rebalance frequency, benchmark, orthogonalization; parameter sweep, Walk-Forward, rule-based backtest (vbt backend).
- **Mining layer**: AI factor mining with LLM, symbolic regression (gplearn), AutoML and text-factor engines, candidate screening, robustness checks and industry neutralization.
- **AI enhancement**: factor AI explanation & Q&A, strategy AI generation / parameter suggestion / review, multi-provider LLM failover.
- **Visualization**: Vue 3 frontend with dashboard, factor library, backtest, mining, data management, macro, comparison and log pages; WebSocket real-time progress push.

## 2. Functional Requirements

### 2.1 Data Management
- One-click full sync (`sync-full`): A-share backfill → indices → macro(broadcast) → financials(fetch+broadcast) → external market factors, staged progress.
- Incremental EOD sync (`eod-sync`), baostock/akshare source, recent N days.
- Index sync & registration (`sync-indices`); ETF sync (`sync-etf`, Tencent qfq alignment + rate-limit protection).
- Macro sync (`macro/sync`): eastmoney indicators → PG narrow table → broadcast to bins.
- Fundamental sync (`fundamental/sync`): akshare financial abstracts → `financial_indicator` → PIT forward-fill broadcast.
- External market overnight factors (`sync-external-market`).
- Validation (`validate`), repair (`repair`), progress/history/stats query, stock search, data preview.

### 2.2 Factor Library
- Factor CRUD (JSON/form add, list, detail, soft delete).
- Evaluation (`evaluate`): IC/RankIC/ICIR/IR/turnover/decay/multi-horizon/orthogonal residual.
- Built-in sets: Alpha158 seed, ETF factor seed, builtin list; batch metric backfill.
- Compare, quantile analysis, neutralization, deep analysis.
- AI explain / batch explain / AI chat / AI detail.
- Export, auto-import, decay check.

### 2.3 Strategy & Backtest
- Strategy CRUD with combination settings and AI preferences.
- Backtest (`backtest`) with qlib/vbt backends and persistent parameter snapshots.
- Results list/detail/trades export/soft delete.
- Parameter sweep with exact dedup caching; Walk-Forward; compare; portfolio report.
- Rule-based strategy backtest (templates + vbt).
- AI strategy generation / parameter suggestion / review.

### 2.4 AI Factor Mining
- LLM / symbolic / AutoML / text engines (rate-limited to 3/min).
- Task list/detail/results; mining templates.
- Task persistence: stale recovery, pending rerun.

### 2.5 System & Ops
- Health check (`/health`): DB/qlib/scheduler/disk/WS/AI.
- Logs: file list, retrieval, clear (rotation + daily cleanup).
- JWT auth with `AUTH_ENABLED` switch; Prometheus `/metrics`; frontend docs page.

### 2.6 Frontend Pages
Dashboard, FactorLibrary, Strategy, StrategyLibrary, Mining, DataStatus, Macro, FactorCompare, BacktestCompare, FactorDeepAnalysis, Docs, Logs, Login.

## 3. Business Rules

- Data sync is **manual-only**; no auto sync at startup/scheduler (fast boot, instant `/health`).
- Sync runs in **independent worker subprocesses** (one per kind), never FastAPI BackgroundTasks; progress bridged via `data/sync_progress.json`.
- baostock crawlers serialized by `sync_lock` flock; obey ≤50k requests/day, no concurrent connections.
- `full` chain re-sets `worker_pid` after each stage to avoid zombie progress.
- Fundamental partial-fetch guard: a code counts as fetched only if it has ≥ half of `FIN_FIELD_NAMES`.
- Bin length must equal `4 + 4×len(day.txt)` bytes; `_pad_bins_to_calendar` NaN-pads after calendar growth.
- Bin writes are atomic (tmp + `os.replace`); calendar-shifting syncs set `calendar_shifting_active` to block backtest/mining.
- `factor` bin is constant 1.0 (prices stored qfq-adjusted).
- Indices/ETFs are a distinct instrument class (OHLCV only), excluded from validation/repair via `stock_index`.
- Factor expressions must pass AST sandbox (reject exec/eval/import, negative `Ref`); expression unique globally.
- CPU/IO-heavy work (evaluation/backtest/qlib reads) dispatched via `run_io_cpu`/`run_cpu`.
- Backtest results persist parameter snapshots; sweep dedups by strategy×period×params.
- Production security gate: default `SECRET_KEY`/`ADMIN_PASSWORD` block startup; `/docs` disabled in production.
- Unified response envelope `ApiResponse {ok, data, error}`.

## 4. Non-Functional Requirements

### 4.1 Performance
- Lightweight startup; `/health` responds immediately.
- Batch evaluation uses asyncio.Queue + single DB writer to avoid pool exhaustion.
- Long syncs run in worker subprocesses; main process only reads progress file.
- Frontend dashboard lazy-loads; factor list cached 5 min.

### 4.2 Availability
- Real-time sync progress (sync_progress.json + WebSocket); retry-safe and idempotent (`ON CONFLICT DO NOTHING`).
- Stale sync/mining tasks auto-recovered on restart; pending mining rerun.
- Validation distinguishes "length anomaly" from "missing field".

### 4.3 Security
- JWT auth (switchable); WebSocket token check.
- SPA static path traversal protection.
- Log rotation and error retrieval; secrets in `.env` only.
