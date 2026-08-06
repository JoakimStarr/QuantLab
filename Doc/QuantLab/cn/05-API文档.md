# API 文档

**项目名称**：QuantLab 量化策略研究平台
**作者**：joakim
**日期**：2026-08-06
**版本**：1.26.806.98

## 变更日志

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| 1.26.806.98 | 2026-08-06 | joakim | 初始版本（文档套件首版） |

---

## 〇、通用约定

- Base URL：`http://localhost:8000/api/v1`
- 响应信封：`{ "ok": bool, "data": object|null, "error": {code, message, status}|null }`
- 错误码：`VALIDATION_ERROR`(422)、`NOT_FOUND`(404)、`QLIB_NOT_AVAILABLE`(503)、`SYNC_IN_PROGRESS`(409)、`AUTH_FAILED`(401)、`RATE_LIMIT_EXCEEDED`(429)
- 鉴权：除登录/状态接口外均依赖 `require_user`；`AUTH_ENABLED=false` 时放行，否则 `Authorization: Bearer <jwt>`
- 数据同步类接口：异步执行，长任务在独立 worker 子进程运行，进度经 `GET /data/sync-progress` 轮询或 WebSocket `/ws` 实时推送
- 生产环境（`APP_ENV=production`）关闭 `/docs`、`/redoc`、`/openapi.json`

## 一、接口总览

| 模块 | 前缀 | 主要接口 |
|------|------|----------|
| 数据管理 | `/quant/data` | sync-full / eod-sync / sync-indices / sync-etf / validate / repair / sync-progress / sync-history / sync-stats / preview / stocks/search / universal / indices / fundamental/sync |
| 行情市场 | `/market` | indices / kline/{code} / overview |
| 宏观指标 | `/macro` | sync / indicators / status / snapshot |
| 因子库 | `/factors` | CRUD / evaluate / seed-builtin / export / auto-import / compare / neutralize / deep-analysis / quantile-analysis / ai-* / decay-check / backfill-alpha158-metrics / etf/seed |
| 策略 | `/strategies` | CRUD / backtest / backtest-results / param-sweep / walk-forward / compare-backtests / portfolio-report / ai/generate / ai/params / ai/review |
| 规则策略 | `/strategy-rules` | templates / backtest |
| 挖掘 | `/mining` | llm / symbolic / automl / text / tasks / templates |
| 日志 | `/logs` | files / list / clear |
| 鉴权 | `/auth` | status / me / ai-status |
| 系统 | `/health` | 健康检查 |
| 配置/文档 | `/config` `/docs` | 配置读取 / 技术文档列表与内容 |
| WebSocket | `/ws` | 实时进度推送 |

## 二、接口详情

### 2.1 数据管理

#### POST /quant/data/sync-full?years=5

**参数**：`years`（回看年数，默认 5）
**说明**：一键全同步链——A 股回填 → 指数 → 宏观(广播) → 财报(拉取+广播) → 外盘情绪因子；异步 worker 子进程执行。
**返回**：`{ok: true, data: {message, kind: "full"}}`；若已有同步进行中返回 409 `SYNC_IN_PROGRESS`。

#### POST /quant/data/eod-sync?source=baostock&days=5

**参数**：`source`（baostock/akshare）、`days`（最近 N 天）
**说明**：增量 EOD 同步。

#### POST /quant/data/sync-indices

**说明**：指数 OHLCV 同步并注册到 `stock_index`。

#### POST /quant/data/sync-etf?years=2

**参数**：`years`（默认 2）
**说明**：全市场 ETF 日 K 同步（腾讯 qfq 对齐回填 + 限流防护）。

#### GET /quant/data/sync-progress

**说明**：读取 `data/sync_progress.json`，返回当前同步任务的 kind/stage/status/进度。
**返回**：`{ok, data: {kind, stage, status, total, processed, message, worker_pid, started_at}}`

#### GET /quant/data/sync-history?limit=20

**说明**：同步历史（`sync_history`）。

#### GET /quant/data/sync-stats

**说明**：同步统计（股票数、最新交易日、数据量等）。

#### GET /quant/data/preview?universe=all&code=&date=

**说明**：行情数据预览（bin 或 PG）。

#### GET /quant/data/stocks/search?q=

**说明**：股票搜索（代码/名称）。

#### GET /quant/data/universes

**说明**：可选标的池列表（all/csiall/csi300/csi500/etf_all...）。

#### GET /quant/data/indices

**说明**：`stock_index` 注册的指数/ETF 列表。

#### GET /quant/data/validate?universe=all

**说明**：跨存储校验——bin 字段/长度、day.txt↔stock_daily↔trade_calendar 对齐、DB↔bin 覆盖度、宏观/财报采样。
**返回**：`{ok, data: {total, issues: [{code, type, detail}], summary}}`。

#### POST /quant/data/repair

**Body**：`{"universe":"all","include_baostock":false}`
**说明**：一键补齐——重建 day.txt + 目标股票 bin、rebuild instruments、rebroadcast 宏观/财报；`include_baostock=true` 时从 baostock 补拉缺失交易日。

#### POST /quant/data/fundamental/sync?broadcast=true

**说明**：财报拉取（akshare）+ 可选广播到 bin（`$roe`/`$netprofit_yoy`...）。

#### GET /quant/data/qlib-status

**说明**：qlib 数据可用性与版本。
**异常**：数据未同步时 503 `QLIB_NOT_AVAILABLE`。

#### GET /quant/data/status

**说明**：各标的池数据新鲜度（`stock_data_status`）。

#### GET /quant/data/external-market / POST /quant/data/sync-external-market

**说明**：外盘隔夜情绪因子状态 / 触发同步（`$us_sp500_ret`、`$us_nasdaq_ret`...）。

### 2.2 因子库

#### GET /factors?category=&status=&page=&size=

**说明**：因子列表（支持分类/状态过滤、分页）。

#### POST /factors

**Body**：`{name, expression, category?, description?}`
**说明**：新增因子。表达式经 AST 沙箱校验（拒绝 exec/eval/import、负周期 Ref），重复表达式 422。
**返回**：`{ok, data: {id, name, expression, ...}}`。

#### GET /factors/{id} / DELETE /factors/{id}

**说明**：因子详情 / 软删除（status=disabled）。

#### POST /factors/seed-builtin

**说明**：导入内置因子集。

#### POST /factors/{id}/evaluate?universe=&start=&end=

**说明**：因子评价（IC/RankIC/ICIR/IR/换手/多周期/正交残差），经执行器派发。
**返回**：`{ok, data: {ic, rank_ic, icir, ir, turnover, decay, ic_by_horizon, orthogonal_ic, evaluated_at}}`。

#### POST /factors/seed-alpha158

**说明**：种子导入 Alpha158 因子集。

#### POST /factors/backfill-alpha158-metrics

**说明**：批量补算 Alpha158 因子指标（asyncio.Queue + 单 DB 写线程）。

#### POST /factors/compare

**Body**：`{factor_ids: [..], start, end}`
**说明**：多因子对比（IC 序列、相关性等）。

#### GET /factors/export

**说明**：因子库导出（CSV/JSON）。

#### POST /factors/auto-import

**说明**：批量自动导入因子表达式。

#### GET /factors/decay-check

**说明**：全部因子的 IC 衰减扫描（每日 18:05 定时任务同源逻辑）。

#### POST /factors/etf/seed

**说明**：ETF 因子种子导入。

#### GET /factors/{id}/decay?max_lag=20

**说明**：单因子 IC 衰减曲线（max_lag≤40）。

#### GET /factors/{id}/quantile-analysis

**说明**：分位数分组收益分析。

#### POST /factors/{id}/neutralize

**Body**：`{benchmark_factor_ids: [...], method}`
**说明**：对基准因子做中性化，返回残差 IC。

#### GET /factors/{id}/deep-analysis

**说明**：深度分析（稳定性、分域、行业/市值分组 IC）。

#### POST /factors/{id}/ai-explain?force=false / POST /factors/ai-explain-batch?factor_ids=&force=

**说明**：AI 解释因子（结构化 JSON 落 `ai_explanation`）；批量版一次解释多个。

#### GET /factors/{id}/ai-detail

**说明**：AI 详情（解释 + 历史）。

#### POST /factors/{id}/ai-chat

**Body**：`{question: string}`
**说明**：AI 追问对话，历史持久化到 `ai_chat_history`。

### 2.3 策略与回测

#### GET /strategies?status=active / POST /strategies

**Body**：`{name, factor_ids, combination_method, topk, n_drop, rebalance_freq, benchmark, orthogonalize, ai_prefs?}`
**说明**：策略列表 / 创建。

#### GET /strategies/{id} / DELETE /strategies/{id}

**说明**：策略详情 / 归档（status=archived）。

#### POST /strategies/{id}/backtest

**Body**：`{start_date, end_date, topk?, n_drop?, rebalance_freq?, combination_method?, backend?("qlib"|"vbt"), initial_capital?, ...}`
**说明**：触发回测。参数快照持久化；进度经 WebSocket 推送。
**返回**：`{ok, data: {result_id}}`。

#### GET /strategies/{id}/backtest-status

**说明**：回测运行状态。

#### GET /strategies/{id}/backtest-results?limit=20

**说明**：该策略的回测结果列表。

#### GET /strategies/backtest-results?limit=20 / GET /strategies/backtest-results/{result_id} / DELETE /strategies/backtest-results/{result_id}

**说明**：全部结果列表 / 详情（净值曲线/指标/成交）/ 软删除。

#### GET /strategies/backtest-statuses

**说明**：全部进行中回测状态（ext 路由，先于 `/{strategy_id}` 注册避免遮蔽）。

#### POST /strategies/{id}/param-sweep

**Body**：`{start_date, end_date, params: [{topk, n_drop, rebalance_freq}, ...]}`
**说明**：参数扫描。精确查重（策略×区间×参数组合命中则复用）。

#### GET /strategies/{id}/param-sweep-results

**说明**：参数扫描结果。

#### POST /strategies/compare-backtests

**Body**：`{result_ids: [...]}`
**说明**：多回测结果对比。

#### GET /strategies/backtest-results/{result_id}/trades

**说明**：逐笔成交明细导出。

#### POST /strategies/{id}/portfolio-report

**说明**：组合报告（持仓/权重/换手分析）。

#### POST /strategies/{id}/walk-forward

**说明**：Walk-Forward 滚动验证（结果持久化 `task_result`）。

#### GET /strategies/{id}/walk-forward-results

**说明**：滚动验证结果。

#### POST /strategies/ai/generate

**Body**：`{factors, style, risk_tolerance, rebalance_pref, capital, ...}`
**说明**：AI 生成策略（偏好写入 `ai_prefs`）。

#### POST /strategies/{id}/ai/params

**说明**：AI 参数建议。

#### POST /strategies/{id}/ai/review

**说明**：AI 回测复盘。

### 2.4 规则策略

#### GET /strategy-rules/templates

**说明**：规则策略模板列表。

#### POST /strategy-rules/backtest

**Body**：`RuleBacktestRequest`
**说明**：规则表达式回测（vbt 后端）。

### 2.5 AI 因子挖掘

#### POST /mining/llm、/mining/symbolic、/mining/automl、/mining/text

**限流**：3/minute（slowapi）
**Body**：`{universe, start_date, end_date, target, ...}`（各引擎参数）
**说明**：提交挖掘任务。任务入 `mining_task` 表，异步执行。
**返回**：`{ok, data: {task_id, status: "pending"}}`。

#### GET /mining/tasks?type=&status=&page=&size=

**说明**：任务列表（按状态/创建时间排序）。

#### GET /mining/tasks/{task_id}

**说明**：任务详情（参数、候选统计、best_ic、result_factor_ids、error）。

#### GET /mining/templates / GET /mining/templates/{key} / POST /mining/templates/{key}/run

**说明**：挖掘模板列表 / 详情 / 运行。

### 2.6 日志

#### GET /logs/files / GET /logs?file=&level=&query=&page=

**说明**：日志文件列表 / 检索（分级过滤、错误检索）。

#### POST /logs/clear?file=error.log

**说明**：清空指定日志文件。

### 2.7 鉴权与系统

#### GET /auth/status

**说明**：鉴权开关状态（前端判断是否需要登录）。

#### GET /auth/me

**说明**：当前用户信息。

#### GET /auth/ai-status

**说明**：AI Provider 配置状态。

#### GET /health、/api/v1/health

**说明**：健康检查 `{status, timestamp, version, checks: {database, qlib, scheduler, disk, ws_connections, ai_providers}}`。

#### GET /config

**说明**：系统配置（脱敏）。

#### GET /docs、/docs/{slug}

**说明**：技术文档列表 / 内容（Markdown）。

#### WS /ws?token=

**说明**：WebSocket 实时推送。客户端周期发 `"ping"` 维持心跳；收 `"pong"` 确认；超时被关（code 4408）。

## 三、异常场景

| 场景 | HTTP | 返回 | 说明 |
|------|------|------|------|
| 参数校验失败 | 422 | VALIDATION_ERROR | 入参不符合 Pydantic schema |
| 资源不存在 | 404 | NOT_FOUND | 因子/策略/结果不存在 |
| 路径不存在 | 404 | NOT_FOUND | Starlette 统一信封 |
| 方法不允许 | 405 | METHOD_NOT_ALLOWED | 统一信封 |
| qlib 数据未同步 | 503 | QLIB_NOT_AVAILABLE | 评价/回测前置检查 |
| 同步进行中 | 409 | SYNC_IN_PROGRESS | 触发同步但已有任务在跑 |
| 未授权 | 401 | AUTH_FAILED | AUTH_ENABLED 下无/坏 token |
| 限流 | 429 | RATE_LIMIT_EXCEEDED | 挖掘接口 3/分钟 |
| 表达式非法 | 422 | VALIDATION_ERROR | AST 沙箱拒绝 |
| 表达式重复 | 422 | VALIDATION_ERROR | uq_factor_expression 冲突 |
| 内部错误 | 500 | INTERNAL_ERROR | 统一 general_error_handler |
