# Test Plan

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

## 1. Scope

- Data: baostock backfill, EOD incremental, calendar padding, validation, repair, index/ETF sync, macro sync, fundamental sync, progress file, crawler lock.
- Factor: expression sandbox, evaluation, Alpha158 backfill, IC cache key, factor validator.
- Backtest: qlib/vbt engines, portfolio report, rule strategy.
- Mining: symbolic, AI strategy, AutoML bundle rebuild.
- System: auth, config compat, log cleanup/retrieval, docs loader, WebSocket manager, DB integration.
- Frontend: lint, SFC parse check, Vite build.

Run:
```bash
DATABASE_URL=postgresql+asyncpg://quantlab:quantlab@localhost:5432/quantlab .venv/bin/python -m pytest
.venv/bin/python -m pytest backend/tests/test_alpha158.py
.venv/bin/python -m pytest backend/tests/test_alpha158.py::TestBackfillAlpha158Metrics::test_backfill_with_ids_no_match
```
`conftest.py` injects a fake qlib module for mock-based tests; DB tests auto-skip without `DATABASE_URL`.

## 2. Unit Test Cases

### 2.1 Data Sync & Validation

| ID | Case | Precondition | Steps | Expected |
|----|------|--------------|-------|----------|
| TC-001 | Backfill idempotency | partial data exists | re-run same window | ON CONFLICT DO NOTHING, no dupes |
| TC-002 | Bin length constraint | stock bins exist | validate 4+4×len(day.txt) | length anomaly reported |
| TC-003 | Calendar growth padding | new trading day | _pad_bins_to_calendar | suspended bins aligned |
| TC-004 | Index/ETF exclusion | index dirs exist | validate/repair | stock-only checks skipped |
| TC-005 | NaN ratio per listed range | new IPO | validate | no false positive |
| TC-006 | Macro upsert idempotent | row exists | re-sync | unique constraint, count unchanged |
| TC-007 | Fundamental partial-fetch guard | truncated fetch | re-sync | <half fields refetched |
| TC-008 | Progress atomic write | idle | trigger eod-sync | status transitions correctly |
| TC-009 | Crawler lock serialization | idle | two concurrent baostock jobs | second → SYNC_IN_PROGRESS |
| TC-010 | Calendar/DB/bin alignment | gaps exist | validate | gaps correctly listed |

### 2.2 Factor Module

| ID | Case | Precondition | Steps | Expected |
|----|------|--------------|-------|----------|
| TC-011 | Sandbox rejects | expression | exec/eval/import/neg Ref | 422 VALIDATION_ERROR |
| TC-012 | Sandbox allows | valid qlib expr | add factor | insert ok |
| TC-013 | Expression unique | existing expr | add again | unique violation |
| TC-014 | Batch backfill no-match | empty set | backfill-alpha158-metrics | idempotent, no side effects |
| TC-015 | IC cache key universe | different universes | eval same factor | keys distinct |

### 2.3 Backtest & Mining

| ID | Case | Precondition | Steps | Expected |
|----|------|--------------|-------|----------|
| TC-016 | qlib snapshot | strategy exists | run backtest | full param snapshot persisted |
| TC-017 | vbt isomorphic output | rule exists | rule backtest | same perf/curve structure |
| TC-018 | Sweep exact dedup | same combo cached | re-sweep | reuse, no rerun |
| TC-019 | Symbolic run | data ready | symbolic task | factors pass IC threshold |
| TC-020 | AI strategy generate | key configured | ai/generate | strategy has ai_prefs |

### 2.4 System

| ID | Case | Precondition | Steps | Expected |
|----|------|--------------|-------|----------|
| TC-021 | Auth switch | AUTH_ENABLED=false | call protected | allowed |
| TC-022 | Production gate | production+default keys | startup | blocked with error |
| TC-023 | Log rotation/cleanup | logs exist | trigger cleanup | expired files removed |
| TC-024 | Log error retrieval | error logs exist | search keyword | hits returned |
| TC-025 | WS heartbeat timeout | no ping | wait | close code 4408 |
| TC-026 | Docs load | md files exist | /docs/{slug} | rendered |

## 3. Concurrency Tests

- **DB pool**: batch backfill + concurrent requests — single DB writer keeps pool healthy.
- **Crawler serialization**: baostock tasks serialized by flock; concurrent triggers execute first only.
- **Backtest/mining concurrency**: CPU work in process pool; verify `/health` stays fast.
- **Calendar shift blocking**: backtest/mining blocked during `calendar_shifting_active`; no half-written data.

## 4. Boundary Tests

| Boundary | Scenario | Expected |
|----------|----------|----------|
| Empty data | validate empty store | summary, no crash |
| Single trading day | 1-day calendar | bin length 8 bytes |
| All suspended day | no trades | NaN pad, no length anomaly |
| New IPO | few days listed | NaN by listed range, no false positive |
| Delisted stock | out_date passed | bin kept, aligned |
| max_lag cap | decay?max_lag=50 | 422 (le=40) |
| result limit cap | limit=500 | 422 (le=100) |
| Mining rate limit | 4 llm calls in a minute | 4th → 429 |
| Empty expression | empty expression add | 422 |

## 5. Test Data

- **Local PG**: `quantlab/quantlab@localhost:5432/quantlab`, tables via `init_db()`.
- **Mock qlib**: fake qlib module injected by `conftest.py`.
- **Bin data**: real backfilled `data/qlib_bin/cn_data/` for integration/validation cases.
- **Special samples**: suspended days, new IPOs (short range), delisted (long range).
- **Note**: `test_macro_api.py::test_macro_upsert_idempotent` writes fixed `(PMI, 2026-07-01, pmi)` and asserts n1==1 — delete the row before rerun or `--deselect` (flaky-by-design).
