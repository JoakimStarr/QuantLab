# API Documentation

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

## 0. Conventions

- Base URL: `http://localhost:8000/api/v1`
- Envelope: `{ "ok": bool, "data": object|null, "error": {code, message, status}|null }`
- Error codes: `VALIDATION_ERROR`(422), `NOT_FOUND`(404), `QLIB_NOT_AVAILABLE`(503), `SYNC_IN_PROGRESS`(409), `AUTH_FAILED`(401), `RATE_LIMIT_EXCEEDED`(429)
- Auth: all business endpoints depend on `require_user`; pass-through when `AUTH_ENABLED=false`, else `Authorization: Bearer <jwt>`.
- Data sync endpoints are async; long tasks run in worker subprocesses; progress via `GET /data/sync-progress` or WebSocket `/ws`.
- Production (`APP_ENV=production`) disables `/docs`, `/redoc`, `/openapi.json`.

## 1. Endpoint Overview

| Module | Prefix | Main endpoints |
|--------|--------|----------------|
| Data | `/quant/data` | sync-full / eod-sync / sync-indices / sync-etf / validate / repair / sync-progress / sync-history / sync-stats / preview / stocks/search / universes / indices / fundamental/sync |
| Market | `/market` | indices / kline/{code} / overview |
| Macro | `/macro` | sync / indicators / status / snapshot |
| Factors | `/factors` | CRUD / evaluate / seed-builtin / export / auto-import / compare / neutralize / deep-analysis / quantile-analysis / ai-* / decay-check / backfill-alpha158-metrics / etf/seed |
| Strategies | `/strategies` | CRUD / backtest / backtest-results / param-sweep / walk-forward / compare-backtests / portfolio-report / ai/generate / ai/params / ai/review |
| Rule strategy | `/strategy-rules` | templates / backtest |
| Mining | `/mining` | llm / symbolic / automl / text / tasks / templates |
| Logs | `/logs` | files / list / clear |
| Auth | `/auth` | status / me / ai-status |
| System | `/health` | health check |
| Config/Docs | `/config` `/docs` | config / docs list & content |
| WebSocket | `/ws` | realtime progress |

## 2. Endpoint Details

### 2.1 Data Management

#### POST /quant/data/sync-full?years=5
`years` (default 5). One-click full sync chain: A-share backfill → indices → macro(broadcast) → financials(fetch+broadcast) → external market; async worker subprocess. Returns `{ok: true, data: {message, kind: "full"}}`; 409 `SYNC_IN_PROGRESS` if already running.

#### POST /quant/data/eod-sync?source=baostock&days=5
Incremental EOD (baostock/akshare).

#### POST /quant/data/sync-indices
Index OHLCV sync + `stock_index` registration.

#### POST /quant/data/sync-etf?years=2
Whole-market ETF daily sync (Tencent qfq alignment + rate-limit protection).

#### GET /quant/data/sync-progress
Reads `data/sync_progress.json`: `{kind, stage, status, total, processed, message, worker_pid, started_at}`.

#### GET /quant/data/sync-history?limit=20 / GET /quant/data/sync-stats
Sync history / statistics.

#### GET /quant/data/preview?universe=&code=&date= / GET /quant/data/stocks/search?q=
Data preview / stock search.

#### GET /quant/data/universes / GET /quant/data/indices
Available pools / registered indices & ETFs.

#### GET /quant/data/validate?universe=all
Cross-store validation (bin fields/length, calendar alignment, coverage, macro/fin sampling). Returns issue list.

#### POST /quant/data/repair
Body `{"universe":"all","include_baostock":false}`. One-click repair: rebuild day.txt + bins, rebuild instruments, rebroadcast macro/fin; optional baostock pull for missing days.

#### POST /quant/data/fundamental/sync?broadcast=true
Financial fetch (akshare) + optional broadcast.

#### GET /quant/data/qlib-status / GET /quant/data/status
qlib availability / pool freshness. 503 `QLIB_NOT_AVAILABLE` when unsynced.

#### GET /quant/data/external-market / POST /quant/data/sync-external-market
External overnight factor status / trigger.

### 2.2 Factors

#### GET /factors?category=&status=&page=&size=
Factor list with filters & pagination.

#### POST /factors
Body `{name, expression, category?, description?}`. AST sandbox validation; duplicate expression → 422.

#### GET /factors/{id} / DELETE /factors/{id}
Detail / soft delete.

#### POST /factors/seed-builtin / POST /factors/seed-alpha158 / POST /factors/etf/seed
Import built-in / Alpha158 / ETF factor sets.

#### POST /factors/{id}/evaluate?universe=&start=&end=
Evaluate IC/RankIC/ICIR/IR/turnover/multi-horizon/orthogonal residual via executor.

#### POST /factors/backfill-alpha158-metrics
Batch metric backfill (Queue + single DB writer).

#### POST /factors/compare
Body `{factor_ids, start, end}`. Multi-factor comparison.

#### GET /factors/export / POST /factors/auto-import
Export / batch auto-import.

#### GET /factors/decay-check
Global IC decay scan.

#### GET /factors/{id}/decay?max_lag=20
Single-factor decay curve (max_lag≤40).

#### GET /factors/{id}/quantile-analysis
Quantile group return analysis.

#### POST /factors/{id}/neutralize
Body `{benchmark_factor_ids, method}`. Neutralize vs benchmarks → residual IC.

#### GET /factors/{id}/deep-analysis
Deep analysis (stability, horizon, industry/market-cap IC).

#### POST /factors/{id}/ai-explain?force=false / POST /factors/ai-explain-batch?factor_ids=&force=
AI explanation (persisted to `ai_explanation`); batch variant.

#### GET /factors/{id}/ai-detail / POST /factors/{id}/ai-chat
AI detail / follow-up chat (persisted to `ai_chat_history`).

### 2.3 Strategies

#### GET /strategies?status=active / POST /strategies
List / create. Body `{name, factor_ids, combination_method, topk, n_drop, rebalance_freq, benchmark, orthogonalize, ai_prefs?}`.

#### GET /strategies/{id} / DELETE /strategies/{id}
Detail / archive.

#### POST /strategies/{id}/backtest
Body `{start_date, end_date, topk?, n_drop?, rebalance_freq?, combination_method?, backend?("qlib"|"vbt"), initial_capital?, ...}`. Persists param snapshot; WS progress. Returns `{result_id}`.

#### GET /strategies/{id}/backtest-status / GET /strategies/{id}/backtest-results?limit=20
Backtest status / results.

#### GET /strategies/backtest-results?limit=20 / GET /strategies/backtest-results/{result_id} / DELETE /strategies/backtest-results/{result_id}
All results / detail (nav/metrics/trades) / soft delete.

#### GET /strategies/backtest-statuses
All running backtests (ext router registered before `/{strategy_id}` to avoid shadowing).

#### POST /strategies/{id}/param-sweep / GET /strategies/{id}/param-sweep-results
Parameter sweep with exact dedup / results.

#### POST /strategies/compare-backtests / GET /strategies/backtest-results/{result_id}/trades
Compare results / export trades.

#### POST /strategies/{id}/portfolio-report / POST /strategies/{id}/walk-forward / GET /strategies/{id}/walk-forward-results
Portfolio report / Walk-Forward / results.

#### POST /strategies/ai/generate / POST /strategies/{id}/ai/params / POST /strategies/{id}/ai/review
AI generate strategy (ai_prefs) / parameter suggestion / review.

### 2.4 Rule Strategy

#### GET /strategy-rules/templates / POST /strategy-rules/backtest
Templates / rule backtest (vbt), body `RuleBacktestRequest`.

### 2.5 Mining

#### POST /mining/llm | /mining/symbolic | /mining/automl | /mining/text
Rate limit 3/min. Submit task → `mining_task` record, async run. Returns `{task_id, status: "pending"}`.

#### GET /mining/tasks?type=&status=&page=&size= / GET /mining/tasks/{task_id}
Task list / detail (candidates, best_ic, result_factor_ids, error).

#### GET /mining/templates / GET /mining/templates/{key} / POST /mining/templates/{key}/run
Template list / detail / run.

### 2.6 Logs

#### GET /logs/files / GET /logs?file=&level=&query=&page=
Log files / retrieval (level filter, keyword search).

#### POST /logs/clear?file=error.log
Clear a log file.

### 2.7 Auth & System

#### GET /auth/status / GET /auth/me / GET /auth/ai-status
Auth switch / current user / AI provider status.

#### GET /health or /api/v1/health
`{status, timestamp, version, checks: {database, qlib, scheduler, disk, ws_connections, ai_providers}}`.

#### GET /config / GET /docs / GET /docs/{slug}
Config (sanitized) / docs list / doc content.

#### WS /ws?token=
Realtime push. Client sends `"ping"` heartbeat, receives `"pong"`; idle connections closed (code 4408).

## 3. Error Scenarios

| Scenario | HTTP | Code | Note |
|----------|------|------|------|
| Validation failed | 422 | VALIDATION_ERROR | Pydantic schema mismatch |
| Not found | 404 | NOT_FOUND | missing entity |
| Unknown path | 404 | NOT_FOUND | Starlette unified envelope |
| Method not allowed | 405 | METHOD_NOT_ALLOWED | unified envelope |
| qlib unsynced | 503 | QLIB_NOT_AVAILABLE | eval/backtest precondition |
| Sync in progress | 409 | SYNC_IN_PROGRESS | concurrent sync trigger |
| Unauthorized | 401 | AUTH_FAILED | missing/bad token |
| Rate limited | 429 | RATE_LIMIT_EXCEEDED | mining 3/min |
| Illegal expression | 422 | VALIDATION_ERROR | sandbox rejection |
| Duplicate expression | 422 | VALIDATION_ERROR | uq_factor_expression |
| Internal error | 500 | INTERNAL_ERROR | general handler |
