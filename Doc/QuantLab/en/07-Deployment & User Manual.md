# Deployment & User Manual

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

# Part 1: Deployment Manual

## 1. Environment Requirements

### 1.1 Runtime

| Item | Requirement |
|------|-------------|
| OS | Linux (verified on WSL) |
| Python | 3.11 (pyqlib doesn't support 3.13) |
| Node.js | ≥ 18 |
| PostgreSQL | 16 (CI) / 18 (local, quantlab/quantlab) |
| Disk | qlib bin ~6GB + PG + backup (data/backup 571MB) |

### 1.2 Dependencies

| Component | Purpose |
|-----------|---------|
| pyqlib | factor computation & backtest |
| baostock / akshare | market & financial data |
| asyncpg / psycopg | PG drivers |
| Alembic | migrations |
| slowapi / apscheduler / fastapi-users / prometheus-client | rate limit / scheduler / auth / metrics |
| Vue 3 / Element Plus / Pinia / vue-echarts | frontend |

## 2. Configuration

### 2.1 `.env` (required, holds secrets)

```
POSTGRES_USER=quantlab
POSTGRES_PASSWORD=quantlab
POSTGRES_DB=quantlab
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
SECRET_KEY=<random>            # required in production
ADMIN_PASSWORD=<strong>
AUTH_ENABLED=false             # true in production
APP_ENV=development            # production disables /docs + enforces keys
OPENCODEZEN_API_KEY=...        # AI providers (any, with failover)
GLM_API_KEY=...
SILICONFLOW_API_KEY=...
```

### 2.2 `config.yaml`
Log level/dir, executor pool sizes, rate limits, qlib provider.

## 3. Deployment Steps

### 3.1 Bootstrap
```bash
./setup.sh   # venv + deps + data dirs + .env
```

### 3.2 Start dev services
```bash
./start.sh dev    # backend :8000 (--reload) + frontend :3000
```
Or separately:
```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000   # cwd=backend
cd frontend && npm run dev
```

### 3.3 Database init
```bash
cd backend
POSTGRES_USER=quantlab POSTGRES_PASSWORD=quantlab POSTGRES_DB=quantlab \
  ../.venv/bin/python -m alembic upgrade head
```
App `init_db()` runs `create_all` first on fresh DBs (baseline migration is empty).

### 3.4 Data sync (manual)
```bash
curl -X POST 'http://localhost:8000/api/v1/quant/data/sync-full?years=5'
curl -X POST 'http://localhost:8000/api/v1/quant/data/eod-sync?source=baostock&days=5'
curl 'http://localhost:8000/api/v1/quant/data/validate?universe=all'
curl -X POST 'http://localhost:8000/api/v1/quant/data/repair' -H 'Content-Type: application/json' \
  -d '{"universe":"all","include_baostock":false}'
```

### 3.5 Production build
```bash
cd frontend && npm run build   # dist/
# mount static dir or set STATIC_DIR; SPA fallback handled by backend
```

## 4. Ops & Monitoring

- **Health**: `GET /health` (DB/qlib/scheduler/disk/WS/AI); debug each `degraded` check.
- **Metrics**: `GET /metrics` (Prometheus).
- **Logs**: `logs/` rotation + daily cleanup; `GET /logs` error search.
- **Sync monitor**: `GET /quant/data/sync-progress` + WebSocket push.
- **Backup**: `pg_dump -Fc data/backup/` (restore with `pg_restore --clean`).

---

# Part 2: User Manual

## 1. Operation Guide

### 1.1 Dashboard
Data freshness, recent backtest/factor summary, health status.

### 1.2 Factor Library
- Add factor via form/JSON (qlib expression), validated by sandbox.
- "Backfill metrics" toolbar applies batch evaluation to selected factors.
- AI explain & follow-up Q&A, persisted.

### 1.3 Strategy Backtest
- Create strategy: factors, combination method, TopK, rebalance, benchmark, orthogonalize.
- Run backtest with qlib/vbt backend; real-time progress.
- Parameter sweep / Walk-Forward / compare / portfolio report / AI review.

### 1.4 AI Mining
- Choose engine (LLM/symbolic/AutoML/text), submit, watch candidates & best_ic.
- Templates one-click run; resulting factors can be added to library.

### 1.5 Data Management
- One-click full sync / EOD / indices / ETF / financials / external buttons.
- Validation + one-click repair; sync stats; index/ETF registry; live progress.

### 1.6 Macro
View PMI/CPI/PPI/GDP/SHIBOR charts; trigger sync.

### 1.7 Comparisons & Deep Analysis
Multi-factor IC comparison; multi-backtest perf comparison; factor robustness (horizon/industry/market-cap).

### 1.8 Docs & Logs
Built-in docs page (Markdown); log search and clear.

## 2. Configuration

| Config | Location | Note |
|--------|----------|------|
| Auth | .env AUTH_ENABLED | false=no login (dev), true=JWT |
| AI providers | .env *_API_KEY | multi-provider failover |
| Sync years | sync-full?years= | default 5 |
| Data source | eod-sync?source= | baostock / akshare |

## 3. Troubleshooting

| Problem | Possible cause | Solution |
|---------|---------------|----------|
| /health degraded | qlib unsynced / DB down / AI key missing | inspect checks; run sync-full |
| 409 on sync | another job running | wait or check sync-progress |
| 503 on eval/backtest | qlib data not ready | sync data first |
| "length anomaly" | calendar growth not padded | run repair or re-eod-sync |
| baostock limit | >50k requests/day | retry next day or use akshare |
| Startup blocked | production + default keys | set strong SECRET_KEY/ADMIN_PASSWORD |
| 429 on mining | rate limit 3/min | wait and retry |
| Logs missing errors | level filter too strict | adjust config.yaml logging.level |
