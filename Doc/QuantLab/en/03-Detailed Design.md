# Detailed Design

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

## 1. Data Sync Pipeline

### 1.1 spawn_sync_worker

1. Validate `kind` (backfill/eod/repair/indices/etf/fundamental/macro/full/external_market).
2. Acquire `sync_lock` flock (baostock jobs serialized).
3. Spawn subprocess with `start_new_session=True`.
4. Write initial progress to `data/sync_progress.json`.

At milestones the subprocess updates progress atomically. On finish: write ok + stats and clear; for `full` orchestration re-set `worker_pid` after each stage (its `finish+clear` would otherwise drop it). On failure write `status: failed` + error; PG writes are `ON CONFLICT DO NOTHING` so reruns only fill gaps.

### 1.2 baostock_backfill

1. Compute trading-date sequence from `years` + `trade_calendar`, newest → oldest.
2. One `query_daily_history_k_AStock` per trading day (whole market, serial).
3. `_build_out_df` outputs 19 baostock fields, qfq-adjusted prices, `factor=1.0`.
4. Write qlib bin (atomic tmp+`os.replace`) + PG `stock_daily`/`stock_basic`/`stock_industry`/`trade_calendar`.
5. Build `instruments/*.txt` pools. Concurrent disk writes for speed; skip already-downloaded days; respect ≤50k requests/day.

### 1.3 eod_incremental

1. Determine missing window from bin tail / PG max date.
2. Fetch per missing trading day.
3. `_sync_stock_bin` merges bins; `_write_calendar` appends days; `_compute_tradable` recomputes tradable (limit-up/down + ST 5% mask).
4. `_pad_bins_to_calendar` NaN-pads stocks without data to keep `4 + 4×len(day.txt)`.

Branches: suspended/delisted stocks only padded (no length anomaly); baostock serial, akshare can run in parallel channel.

### 1.4 full_sync orchestration

```
stage1 backfill → stage2 indices → stage3 macro(fetch+broadcast)
→ stage4 financials(fetch+broadcast) → stage5 external market
```
Each stage updates progress independently; `worker_pid` re-set between stages; failure marks overall failed but preserves completed stages (idempotent resume).

### 1.5 macro_sync

1. Pull indicators (PMI/CPI/PPI/GDP/SHIBOR...) → `macro_indicator` narrow table, upsert on `(indicator, report_date, field_name)`.
2. `available_date = report_date + release delay` (PMI 0 / CPI-PPI 9 / GDP 45 days) as PIT anchor.
3. Broadcast via PIT forward-fill to `features/{code}/$pmi.day.bin` etc.

### 1.6 fundamental_sync

1. akshare per-stock abstracts → `financial_indicator` narrow table.
2. Partial-fetch guard: code marked fetched only if ≥ half of `FIN_FIELD_NAMES` present.
3. Broadcast with `available_date = report_date + disclosure delay`, PIT forward-fill `$roe`/`$netprofit_yoy` etc.

### 1.7 external_market

Pull US index overnight moves; compute `$us_sp500_ret`, `$us_nasdaq_ret`; broadcast PIT-aligned to next A-share trading day.

### 1.8 validation

| Check | Logic |
|-------|-------|
| bin fields | 19 stock fields per dir (indices/ETFs excluded via `stock_index`) |
| length | `filesize == 4 + 4×len(day.txt)` |
| calendar alignment | day.txt ↔ stock_daily ↔ trade_calendar |
| DB↔bin coverage | bidirectional code diff |
| NaN ratio | measured against own listed range (no false-flag on new IPOs) |
| macro/fin sampling | spot-check `$pmi`/`$roe` presence/value |

## 2. Factor Module

### 2.1 expression AST sandbox

Parse expression → walk AST → reject `exec/eval/import` and negative-period `Ref` → allow insert; `uq_factor_expression` prevents duplicates.

### 2.2 factor_eval

- IC/RankIC via `run_io_cpu` qlib compute vs forward returns (configurable horizon); ICIR = mean/std; decay = multi-lag IC JSON; ic_by_horizon multi-horizon.
- `AutoML(...)` expressions load trained bundles from `data/models/automl/{task_id}.pkl`; `_resolve_task_id_from_factor_ids()` reads legacy SQLite mapping (post-migration leftover; should point at PG).
- Batch backfill uses asyncio.Queue + single DB writer to avoid pool exhaustion.

### 2.3 neutralize / orthogonalize

Neutralize regresses out benchmark factors; orthogonalize applies Gram-Schmidt to existing benchmarks producing incremental alpha (`orthogonal_ic`).

## 3. Backtest Module

### 3.1 Engine factory

```
create_backtest_engine(backend) → QlibBacktestEngine | VbtBacktestEngine
```

### 3.2 qlib_backtest

Build dataset from `calendars/day.txt` + `instruments`; TopK selection, rebalance frequency, weights (equal/ic/lightgbm/stacking), benchmark, orthogonalization; outputs nav curve, metrics, trades; persists parameter snapshot.

### 3.3 vbt_backtest

Rule expressions executed via vbt; signal-day execution (ETF T+1 constraint handled on qlib side); isomorphic output for comparison.

### 3.4 param_sweep

Cartesian product of candidates; exact dedup on `(strategy_id, start, end, topk, rebalance_freq)` via `idx_backtest_sweep_lookup`; parallel execution; progress persisted in `task_result` (`task_type=param-sweep`).

### 3.5 walk_forward

Rolling train/validation windows; results persisted in `task_result` (`task_type=walk-forward`).

## 4. Mining Module

- **LLM**: prompt with style/history/feedback constraints → multi-provider failover (opencodezen/glm/siliconflow) → candidate expressions → sandbox → quick IC screen → insert `factor` (source_task_id).
- **Symbolic**: gplearn evolution, multi-objective (IC/complexity); rolling revalidation + industry neutralization + candidate dedup.
- **AutoML**: train model bundles from candidates; duplicate factor removed and original metrics restored (rebuild_automl_bundles script as backstop).
- **Text**: news/announcement text → sentiment/topic numeric factors.

## 5. Recovery & Scheduling

- `recover_stale_sync` / `recover_stale_mining`: on startup scan running tasks whose pid is gone → mark failed; `rerun_pending_mining` reschedules pending.
- APScheduler: stale-mining reaper every 10 min; factor decay check daily 18:05. **No automatic data sync.**

## 6. Health & Observability

`_build_health_payload`: DB ping / qlib availability / scheduler / disk / WS count / AI provider keys → `degraded` on any failure. Prometheus `/metrics`: request counts, pool usage, slow queries. Logs: size rotation + daily cleanup + error retrieval.

## 7. Security Design

1. Production security gate blocks default keys.
2. Swagger/OpenAPI disabled in production.
3. SPA static path traversal protection.
4. JWT via `require_user`; WS token check.
5. Unified error handlers (AppError/Validation/HTTP/Starlette 404/405) → `ApiResponse` envelope.
