# Database Design

**Project**: QuantLab Quantitative Strategy Research Platform
**Author**: joakim
**Date**: 2026-08-06
**Version**: 1.26.806.98

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.26.806.98 | 2026-08-06 | joakim | Initial version |

---

## 0. Storage Overview

- **Business DB (PostgreSQL)**: covered here. asyncpg (app) / psycopg (Alembic).
- **Market DB (qlib bin)**: `data/qlib_bin/cn_data/features/{code}/{field}.day.bin` float32 + `calendars/day.txt` + `instruments/*.txt`.
- Convention: tables built by `Base.metadata.create_all`; Alembic migrations are additive and defensive (`sa.inspect()`), never modifying applied migrations.

## 1. Core Tables

### 1.1 stock_daily (A-share daily full fields)

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| code | String(16) | PK | QLib code sh600000 |
| trade_date | Date | PK | trading date |
| open/high/low/close | Float | NULL | OHLC |
| preclose | Float | NULL | prev close |
| volume / amount | Float | NULL | volume(shr) / amount(CNY) |
| adjustflag | Integer | NULL | 1 back / 2 forward / 3 unadjusted |
| turn | Float | NULL | turnover(%) |
| tradestatus | Integer | NULL | 1 normal / 0 suspended |
| pct_chg | Float | NULL | change(%) |
| is_st | Boolean | NULL | ST flag |
| pe_ttm / pb_mrq / ps_ttm / pcf_ncf_ttm | Float | NULL | valuations |

Index: `idx_stock_daily_date(trade_date)`.

### 1.2 etf_daily (ETF narrow daily)

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| code | String(16) | PK | sh510300 |
| trade_date | Date | PK | trading date |
| open/high/low/close | Float | NULL | OHLC |
| volume / amount | Float | NULL | vol(shares)/amount |
| pct_chg | Float | NULL | change(%) |

Index: `idx_etf_daily_date(trade_date)`. Kept separate from stock_daily (ETFs lack stock BIN_FIELDS/financials).

### 1.3 stock_basic

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| code | String(16) | PK | security code |
| name | String(64) | NULL | name |
| ipo_date / out_date | Date | NULL | listing/delisting |
| type | String(8) | NULL | 1 stock / 2 index / 3 other / 4 bond / 5 ETF |
| status | String(8) | NULL | 1 listed / 0 delisted |

### 1.4 stock_industry

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| code | String(16) | PK | code |
| code_name | String(64) | NULL | name |
| industry | String(64) | NULL | industry |
| industry_classification | String(8) | NULL | sw / zjh |
| update_date | Date | NULL | update date |

### 1.5 trade_calendar

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| trade_date | Date | PK | calendar date |
| is_trading_day | Boolean | NULL | trading flag |

### 1.6 macro_indicator (narrow table)

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| indicator | String(32) | NOT NULL | PMI/CPI/PPI/GDP |
| report_date | Date | NOT NULL | report period |
| field_name | String(64) | NOT NULL | field name |
| value | Float | NULL | value |
| unit | String(16) | NULL | unit |
| available_date | Date | NULL | PIT anchor |
| source | String(32) | NULL | data source |

Constraint: `uq_macro_indicator(indicator, report_date, field_name)`; index `idx_macro_indicator_date(indicator, report_date)`. Design: any indicator×field in one table — no DDL for new indicators; `available_date` prevents look-ahead.

### 1.7 financial_indicator (quarterly narrow table)

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| code | String(16) | NOT NULL | sh600000 |
| report_date | Date | NOT NULL | quarter end |
| field_name | String(64) | NOT NULL | field (same as bin broadcast) |
| value | Float | NULL | value |
| unit | String(16) | NULL | unit |
| available_date | Date | NULL | PIT date |
| source | String(32) | NULL | source |

Constraint: `uq_financial_indicator(code, report_date, field_name)`; index `idx_fin_indicator_code_date(code, available_date)`.

### 1.8 stock_index (index/ETF registry)

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| code | String(16) | NOT NULL | qlib code (lowercase) |
| name | String(64) | NULL | name |
| source | String(32) | NULL | baostock/akshare |
| type | String(16) | NOT NULL default 'index' | index/etf |
| created_at / updated_at | DateTime | server_default now() | audit |

Index: `uq_stock_index_code(code)` unique. Used by validation/repair to distinguish stocks from indices/ETFs.

### 1.9 factor

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| name | String | NOT NULL | name |
| expression | Text | NOT NULL, UNIQUE | qlib expression |
| category | String | NOT NULL default 'builtin' | builtin/llm/symbolic/text |
| description | Text | NULL | summary |
| ai_explanation | Text | NULL | AI explanation JSON |
| ai_chat_history | Text | NULL | AI chat JSON array |
| ic / rank_ic / icir / ir / turnover | Float | NULL | metrics |
| decay | Text | NULL | IC decay JSON |
| ic_by_horizon | Text | NULL | multi-horizon IC JSON |
| orthogonal_ic | Float | NULL | residual IC |
| eval_start / eval_end | String | NULL | eval window |
| evaluated_at | TIMESTAMP | NULL | eval time |
| status | String | default 'active' | active/disabled |
| source_task_id | Integer | NULL | source mining task |
| created_at | TIMESTAMP | default now() | created |

Indexes: `idx_factor_category_status`, `idx_factor_ic`, `idx_factor_name`, `idx_factor_source_task`, `uq_factor_expression`.

### 1.10 strategy

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| name | String | NOT NULL | name |
| description | Text | NULL | description |
| factor_ids | Text | NOT NULL default '[]' | factor id JSON |
| combination_method | String | default 'equal_weight' | equal_weight/ic_weight/lightgbm/stacking |
| topk | Integer | default 50 | topk |
| n_drop | Integer | default 5 | n_drop |
| rebalance_freq | String | default 'day' | day/week/month |
| benchmark | String | default 'SH000300' | benchmark |
| orthogonalize | Integer | default 0 | Gram-Schmidt switch |
| ai_prefs | Text | NULL | AI prefs JSON |
| status | String | default 'active' | active/archived |
| created_at / updated_at | TIMESTAMP | - | audit |

Index: `idx_strategy_status`.

### 1.11 mining_task

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| type | String | NOT NULL | llm/symbolic/text/automl |
| status | String | default 'pending' | pending/running/done/failed |
| params | Text | NULL | params JSON |
| candidates_generated / candidates_passed | Integer | default 0 | counts |
| best_ic | Float | NULL | best IC |
| result_factor_ids | Text | NULL | factor ids JSON |
| error | Text | NULL | error |
| started_at / finished_at | TIMESTAMP | NULL | times |
| created_at | TIMESTAMP | default now() | created |

Indexes: `idx_mining_type_status`, `idx_mining_created_at`, `idx_mining_status_created`.

### 1.12 backtest_result

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| strategy_id | Integer | FK→strategy.id NOT NULL | strategy |
| start_date / end_date | String | NOT NULL | period |
| topk / n_drop / rebalance_freq | - | NULL | param snapshot |
| combination_method / orthogonalize / benchmark / backend | - | NULL | snapshot |
| initial_capital | Float | NULL | capital |
| annual_return / annual_volatility / sharpe / sortino / max_drawdown / calmar / turnover / win_rate / benchmark_return / excess_return | Float | NULL | metrics |
| nav_curve | Text | NULL | nav JSON |
| metrics | Text | NULL | full metrics JSON |
| trades | Text | NULL | trades JSON |
| created_at | TIMESTAMP | default now() | created |
| is_deleted | Integer | NOT NULL default 0 | soft delete |
| deleted_at | TIMESTAMP | NULL | deleted time |

Indexes: `idx_backtest_strategy`, `idx_backtest_created_at`, `idx_backtest_strategy_period`, `idx_backtest_sweep_lookup`.

### 1.13 user

fastapi-users `SQLAlchemyBaseUserTable` (email, hashed_password, is_active, is_superuser, is_verified), explicit Integer PK.

### 1.14 stock_data_status

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| universe | String | UNIQUE | pool/code |
| latest_date / row_count / stock_count | - | NULL | freshness |
| last_updated | TIMESTAMP | NULL | updated |
| status | String | default 'ok' | ok/syncing/failed/empty |
| last_error | String | NULL | error |
| qlib_dir | String | NULL | qlib dir |
| last_sync_path | String | NULL | path |
| sync_trigger | String | NULL | manual/auto |

Constraint: `uq_stock_data_status_universe`.

### 1.15 sync_history

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| universe / data_source / sync_path | String | - | pool/source/path |
| status | String | NOT NULL | running/ok/failed |
| started_at / finished_at | TIMESTAMP | - | times |
| duration_seconds | Float | NULL | duration |
| version / release_date / latest_date | String | NULL | versions |
| stock_count / row_count | Integer | NULL | stats |
| file_size_mb | Float | NULL | size |
| error | Text | NULL | error |

Indexes: `idx_sync_history_universe`, `idx_sync_history_status`, `idx_sync_history_universe_started`.

### 1.16 task_result

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| id | Integer | PK auto | PK |
| strategy_id | Integer | NULL | strategy |
| task_type | String | NOT NULL | param-sweep / walk-forward |
| status | String | NOT NULL default 'running' | running/done/failed |
| payload | Text | NULL | JSON result |
| error | Text | NULL | error |
| created_at / updated_at | TIMESTAMP | - | audit |

Index: `idx_task_result_strategy_type`. Replaces in-memory settings singleton; readable across restarts/workers.

## 2. Relationships

```
stock_index (registry distinguishing index/etf)
    │ excluded during validation/repair
    ▼
features/* (qlib bin dirs, not PG tables)

strategy 1 ──< backtest_result N (FK strategy_id, soft delete)
mining_task 1 ──< factor N (source_task_id reverse link, not strict FK)
strategy 1 ──< task_result N (strategy_id)

Narrow-table family (isomorphic):
  macro_indicator     (indicator, report_date, field_name) UNIQUE
  financial_indicator (code, report_date, field_name) UNIQUE
```

## 3. Index Recommendations

| Table | Columns | Type | Purpose |
|-------|---------|------|---------|
| stock_daily | trade_date | single | whole-market by day |
| etf_daily | trade_date | single | ETF by day |
| macro_indicator | indicator, report_date | composite | indicator series |
| financial_indicator | code, available_date | composite | PIT forward-fill |
| stock_index | code | unique | registry dedup |
| factor | expression | unique | dedup |
| factor | category, status | composite | filter |
| factor | source_task_id | single | reverse lookup |
| backtest_result | strategy_id, start_date, end_date, topk, rebalance_freq | composite | sweep dedup |
| mining_task | status, created_at | composite | task list |
| sync_history | universe, started_at | composite | recent records |

## 4. Migration Strategy

- Baseline `23fc4c667c2f` is an **empty** upgrade; fresh DBs run `create_all` then `alembic upgrade head`.
- Subsequent migrations use `sa.inspect()` existence checks; additive columns/indexes only; never modify applied migrations.
- `b5e1f7g8h9i0` was rewritten to use `sa.inspect()` — do not revert to raw `bind.execute(...)` (raises `ObjectNotExecutableError` under SQLAlchemy 2.0 + psycopg).
