---
title: API 参考
slug: api-reference
order: 5
group: API
summary: 后端 API 接口完整参考 —— 涵盖 65 个端点 / 13 个模块
---

# API 参考

> **版本对应**：FastAPI 0.115+ · Python 3.11+ · qlib >= 0.9
> 所有路由统一挂载在前缀 `/api/v1` 之下；本文件列出的路径均为**去除前缀后**的形式（开发环境拼接 `http://localhost:8000/api/v1`）。

## 目录

1. [通用约定](#1-通用约定)
2. [认证 Auth](#2-认证-authpy--4-端点)
3. [运行时配置 Config](#3-运行时配置-configpy--1-端点)
4. [技术文档 Docs](#4-技术文档-docspy--2-端点)
5. [数据管理 Quant Data](#5-数据管理-quant-datapydatapy--13-端点)
6. [市场行情 Market](#6-市场行情-marketpy--3-端点)
7. [日志 Logs](#7-日志-logspy--2-端点)
8. [因子库 Factor](#8-因子库-factorpy--14-端点)
9. [策略 Strategy](#9-策略-strategypy--14-端点)
10. [AI 因子挖掘 Mining](#10-ai-因子挖掘-miningpy--9-端点)
11. [附录：枚举值与错误码](#11-附录枚举值与错误码)

---

## 1. 通用约定

### 1.1 基础 URL

| 环境 | Base URL |
|------|----------|
| 开发（FastAPI 直起） | `http://localhost:8000/api/v1` |
| 生产（Nginx 反代） | 反代域名 `/api/v1` |
| OpenAPI 文档 | `/docs`（Swagger UI）、`/redoc`、`/openapi.json` |

### 1.2 响应格式

所有响应严格遵循 `ApiResponse[T]` 统一信封（Pydantic 模型定义于 `app/schemas/common.py`）。

**成功**：

```json
{
  "ok": true,
  "data": { /* T 类型对象 */ },
  "error": null
}
```

**失败**：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "HTTP_ERROR",
    "message": "Not Found",
    "status": 404
  }
}
```

### 1.3 认证

- 通过环境变量 `AUTH_ENABLED` 控制（默认 `false`）。
  - `false`：除部分状态探测接口外**全部放行**（开发态）。
  - `true`：除 `/auth/login`、`/auth/status`、`/auth/ai-status`、`/docs`、`/docs/{slug}`、`/config`、`/openapi*` 外，**所有端点强制 Bearer Token**。
- Token 通过 `POST /auth/login` 获取（bcrypt 校验口令，速率限制 `login_rate_limit`）。
- Token 类型：`bearer`，有效期 **7 天**（86400 × 7 秒）。
- 调用时携带：`Authorization: Bearer <token>`。
- 前端通过 `GET /auth/status` 探测当前是否启用了鉴权，从而决定是否跳转登录页。

### 1.4 速率限制（slowapi）

| 端点 | 限制 |
|------|------|
| `POST /auth/login` | `settings.login_rate_limit`（默认 `"5/minute"`） |
| `POST /mining/llm` | `3/minute` |
| `POST /mining/symbolic` | `3/minute` |
| `POST /mining/automl` | `3/minute` |
| `POST /mining/text` | `3/minute` |

被限流的请求会返回 HTTP **429**。

### 1.5 分页 / 通用 Query 参数

| 参数 | 默认 | 上限 | 说明 |
|------|------|------|------|
| `limit` | 100（个别端点 20/30/50） | 1000 | 返回条数 |
| `offset` | 0 | — | 起始偏移 |
| `status` | 见各端点 | — | 过滤枚举（如 `active`/`disabled`/`archived`） |

### 1.6 后台任务

带「**后台执行**」标记的端点会立刻返回提交确认，把真正的 CPU/IO 工作交给 `BackgroundTasks`。
- 进度查询：`GET /quant/data/sync-progress`。
- 任务结果：通过 `GET /mining/tasks/{task_id}` 或 `GET /strategies/{strategy_id}/backtest-status` 等端点轮询。

---

## 2. 认证（`auth.py`，4 端点）

### 2.1 `GET /auth/status`
- **鉴权**：公开（用于前端判断是否需登录）
- **说明**：返回 `auth_enabled` 标记。
- **响应**：
```json
{"ok": true, "data": {"auth_enabled": false}}
```

### 2.2 `POST /auth/login`
- **鉴权**：公开；速率限制
- **说明**：密码登录，返回 JWT。
- **请求体**（`LoginRequest`）：
```json
{"password": "your-admin-password"}
```
- **响应 200**：
```json
{"ok": true, "data": {"token": "eyJhbGc...", "token_type": "bearer"}}
```
- **错误**：
  - `401 AUTH_FAILED` —— 密码错误

### 2.3 `GET /auth/me`
- **鉴权**：必需
- **说明**：返回当前 token 解析出的角色与过期时间。
- **响应**：
```json
{"ok": true, "data": {"role": "admin", "exp": 1735689600}}
```

### 2.4 `GET /auth/ai-status`
- **鉴权**：公开（给 Mining 页 badge 用）
- **说明**：探测可用 AI Provider：依次为 opencodezen / glm / siliconflow。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "providers": [
      {"provider": "opencodezen", "model": "...", "ready": true},
      {"provider": "glm", "model": "...", "ready": true}
    ],
    "count": 2
  }
}
```

---

## 3. 运行时配置（`config.py`，1 端点）

### 3.1 `GET /config`
- **鉴权**：公开
- **说明**：返回前端展示用的统一版本/时区/API 前缀，避免前后端版本号不一致。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "name": "QuantLab",
    "version": "0.5.0",
    "description": "...",
    "timezone": "Asia/Shanghai",
    "api_version": "v1"
  }
}
```

---

## 4. 技术文档（`docs.py`，2 端点）

> 由后端 `app/services/docs/loader.py` 扫描 `~/QuantLab/docs/*.md`，按 frontmatter `order` 排序。

### 4.1 `GET /docs`
- **鉴权**：公开
- **说明**：列出所有技术文档（不含正文）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "docs": [
      {"slug": "data-layer", "title": "数据层", "order": 1, "group": "架构",
       "summary": "...", "file": "DATA_LAYER.md"}
    ]
  }
}
```

### 4.2 `GET /docs/{slug}`
- **鉴权**：公开
- **路径参数**：`slug` —— 文档标识，如 `data-layer`、`api-reference`
- **说明**：返回文档的 Markdown 正文。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "slug": "api-reference",
    "title": "API 参考",
    "order": 5,
    "group": "API",
    "summary": "...",
    "content": "...",
    "file": "API_REFERENCE.md"
  }
}
```
- **错误**：`404` —— 文档不存在。

---

## 5. 数据管理（`quant_data.py` + `data_ext.py`，13 端点）

> 前缀：`/quant/data`；几乎全部依赖 qlib，依赖缺失会抛 `503 QLIB_NOT_AVAILABLE`。

### 5.1 `GET /quant/data/qlib-status`
- **说明**：探测 qlib 是否安装/初始化成功；顺带读 `calendars/day.txt` 给首末日期。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "available": true,
    "message": "qlib 已就绪",
    "provider_uri": "/home/joakim/qlib_data/cn_data",
    "earliest_date": "2005-01-04",
    "calendar_count": 4872
  }
}
```

### 5.2 `GET /quant/data/status`
- **说明**：股票池数据新鲜度（自动把超过 30 分钟仍 `syncing` 的标记 `failed`）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "universe": "csi300",
        "latest_date": "2026-07-30",
        "row_count": 1500000,
        "stock_count": 300,
        "last_updated": "2026-07-30T18:42:11+08:00",
        "status": "ready",
        "last_error": null,
        "qlib_dir": "..."
      }
    ],
    "total": 1
  }
}
```

### 5.3 `POST /quant/data/sync`
- **说明**：触发股票数据同步到 qlib bin（**后台执行**，数据源由 `config.quant.data_source` 决定）。
- **请求体**（`SyncDataRequest`）：
```json
{
  "start_date": "2020-01-01",
  "end_date": "2026-07-30",
  "codes": null,
  "universe": "csi300",
  "days": 30
}
```
- **响应**：
```json
{
  "ok": true,
  "data": {
    "message": "已触发 universe=csi300 数据同步（后台执行，数据源=chenditc）",
    "universe": "csi300",
    "data_source": "chenditc",
    "start_date": "2020-01-01",
    "end_date": "2026-07-30"
  }
}
```
- **错误**：
  - `503 QLIB_NOT_AVAILABLE`
  - `409 SYNC_IN_PROGRESS` —— 同一 universe 在 10 分钟内已发起过同步

### 5.4 `GET /quant/data/sync-progress`
- **说明**：读取当前后台同步的实时进度。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "running": true,
    "universe": "csi300",
    "stage": "downloading",
    "percent": 42.7,
    "message": "downloading chenditc/investment_data ...",
    "updated_at": "2026-07-30T18:42:11+08:00"
  }
}
```

### 5.5 `GET /quant/data/preview`
- **Query**：
  - `code` —— 股票代码（如 `SH600000`）或股票池名（如 `csi300`，默认）
  - `limit` —— 默认 30，上限 100
- **说明**：取指定股票/股票池最近 N 天 OHLCV。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {"date": "2026-07-30", "code": "SH600000", "open": 10.2,
       "close": 10.35, "high": 10.4, "low": 10.15, "volume": 12345678, "factor": 1.0}
    ],
    "code": "SH600000",
    "count": 30
  }
}
```

### 5.6 `GET /quant/data/sync-history`
- **Query**：`limit`（默认 50，上限 200）
- **说明**：拉取最近的同步历史（`SyncHistory` 表）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [{
      "id": 12, "universe": "csi300", "data_source": "chenditc",
      "status": "done",
      "started_at": "2026-07-30T18:30:00+08:00",
      "finished_at": "2026-07-30T18:42:11+08:00",
      "duration_seconds": 731,
      "version": "v3",
      "latest_date": "2026-07-30",
      "stock_count": 300, "row_count": 1500000, "file_size_mb": 38.2,
      "error": null
    }],
    "total": 1
  }
}
```

### 5.7 `PUT /quant/data/data-source`
- **Query**：`source` —— 必填，`chenditc` 或 `akshare`
- **说明**：动态切换数据源，写回 `config.yaml` 并同步更新 `settings.quant.data_source`。
- **响应**：
```json
{"ok": true, "data": {"data_source": "akshare", "message": "数据源已切换为 akshare"}}
```
- **错误**：`422 VALIDATION_ERROR`

### 5.8 `GET /quant/data/data-source`
- **说明**：查询当前数据源。
- **响应**：
```json
{"ok": true, "data": {"source": "chenditc"}}
```

### 5.9 `POST /quant/data/incremental-sync`
- **说明**：在已有数据基础上做一次增量同步（**后台执行**）。
- **响应**：`{"ok": true, "data": {"message": "增量同步已提交"}}`

### 5.10 `POST /quant/data/eod-sync`
- **Query**：
  - `universe`（默认 `csi300`）—— `csi300` / `csi500` / `all`
  - `days`（默认 5，1–30）
  - `overwrite`（默认 false）
- **说明**：通过 akshare 国内源拉取最近 N 个交易日的 OHLCV，与 chenditc 全量同步互补。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "message": "EOD增量同步已提交（universe=csi300, days=5），后台执行中",
    "universe": "csi300", "days": 5, "overwrite": false
  }
}
```

### 5.11 `POST /quant/data/sync-indices`
- **说明**：同步主要指数（上证、深证、沪深300 等）日K数据到 qlib bin（**后台执行**）。
- **响应**：`{"ok": true, "data": {"message": "指数同步已提交，后台执行中"}}`

### 5.12 `GET /quant/data/integrity-check`
- **Query**：`universe`（可选）
- **说明**：逐只股票校验 qlib bin 文件长度是否与日历天数一致，输出缺失日期、长度异常等明细。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "total": 300, "ok": 298, "missing_dates": 2,
    "anomalies": [{"code": "SH600000", "expected_days": 4872, "actual_days": 4871}],
    "summary": "2 只股票数据缺失日期"
  }
}
```

### 5.13 `POST /quant/data/sync-industry`
- **说明**：通过 akshare 拉取申万一级行业分类，写到 `data/industry_map.json`，供因子行业中性化使用。
- **响应**：
```json
{"ok": true, "data": {"ok": true, "industries": 31, "saved": "data/industry_map.json"}}
```

---

## 6. 市场行情（`market.py`，3 端点）

### 6.1 `GET /market/indices`
- **说明**：返回支持的指数清单（沪深300、上证50、中证500、中证1000、深证成指、创业板指、科创50、上证指数）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {"code": "SH000300", "name": "沪深300", "desc": "CSI 300", "qlib_code": "sh000300"}
    ]
  }
}
```

### 6.2 `GET /market/kline/{index_code}`
- **路径**：`index_code` —— `SH000300` 等
- **Query**：
  - `period` —— `1d`（默认）/ `1w` / `1M`
  - `start_date` / `end_date` —— `YYYY-MM-DD`
  - `limit` —— 默认 120，范围 [1, 500]
- **说明**：从 qlib 读取指数 OHLCV 并按周期聚合；自动计算 `pct_change`。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "index_code": "SH000300",
    "index_name": "沪深300",
    "period": "1d",
    "count": 120,
    "items": [
      {"date": "2026-07-30", "open": 3850.1, "high": 3865.0, "low": 3842.3,
       "close": 3861.4, "volume": 12345678, "pct_change": 0.32}
    ]
  }
}
```
- **错误**：`400 UNSUPPORTED_INDEX`、`503 QLIB_NOT_AVAILABLE`

### 6.3 `GET /market/overview`
- **说明**：返回所有支持指数的最新一日收盘 + 涨跌幅（用于首页大屏）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {"code": "SH000300", "name": "沪深300", "price": 3861.4, "pct_change": 0.32}
    ]
  }
}
```

---

## 7. 日志（`logs.py`，2 端点）

### 7.1 `GET /logs/files`
- **说明**：列出允许查询的日志文件（白名单：`app.log`、`error.log`、`api.jsonl`、`perf.jsonl`、`audit.jsonl`）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {"name": "error.log", "size": 12345, "size_human": "12.1 KB", "modified": 1735689600}
    ]
  }
}
```

### 7.2 `GET /logs`
- **Query**：
  - `file` —— 默认 `error.log`（必须命中白名单）
  - `level` —— `ERROR` / `WARNING` / `INFO` / `DEBUG`（按级别阈值过滤）
  - `search` —— 关键词模糊搜索
  - `request_id` —— 按 request_id 精确过滤
  - `limit`（默认 100，上限 500）
  - `offset`（默认 0）
- **说明**：从文件末尾向前读取（> 5MB 自动 tail），合并多行 traceback，按时间倒序返回。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "timestamp": "2026-07-30 18:42:11,234",
        "level": "ERROR",
        "logger": "app.api.factor",
        "message": "因子评价失败 factor_id=12",
        "request_id": "a1b2c3d4e5f6",
        "traceback": "Traceback (most recent call last):\n  ..."
      }
    ],
    "total": 1,
    "file": "error.log"
  }
}
```
- **错误**：`400 INVALID_FILE`

---

## 8. 因子库（`factor.py` + `factor_ext.py`，14 端点）

> 前缀：`/factors`；评价类端点依赖 qlib。

### 8.1 `GET /factors`
- **Query**：
  - `category` —— 类别过滤
  - `status` —— 默认 `active`
  - `sort_by` —— 默认 `ic`
  - `limit`（默认 100，上限 500）
  - `offset`（默认 0）
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {"id": 12, "name": "Momentum_20", "category": "builtin",
       "expression": "$close / Ref($close, 20) - 1",
       "ic": 0.0432, "rank_ic": 0.051, "icir": 1.8,
       "turnover": 0.22, "status": "active",
       "created_at": "2026-07-01T10:00:00+08:00"}
    ],
    "total": 42
  }
}
```

### 8.2 `GET /factors/{factor_id}`
- **路径**：`factor_id`
- **响应 200**：单个因子完整信息（字段同上）
- **响应 404**：`NOT_FOUND`

### 8.3 `POST /factors`
- **Query**：
  - `name`（必填）
  - `expression`（必填，会校验 qlib 表达式合法性）
  - `category`（默认 `builtin`）
  - `description`（可选）
- **错误**：`422 EXPR_INVALID` —— 表达式非法（如未注册算子、引用不存在的字段）

### 8.4 `DELETE /factors/{factor_id}`
- **说明**：软删除，状态置为 `disabled`。

### 8.5 `POST /factors/seed-builtin`
- **说明**：导入内置因子集（动量/反转/换手/波动率等）。返回新增/已存在的数量。

### 8.6 `POST /factors/{factor_id}/evaluate`
- **Query**：`start_date`、`end_date`（默认走 `default_backtest_period`）
- **说明**：把 IC/RankIC/ICIR/换手率等指标写回因子库（**后台执行**）。
- **错误**：`404 NOT_FOUND`、`503 QLIB_NOT_AVAILABLE`

### 8.7 `POST /factors/compare`
- **Query**：
  - `factor_ids`（必填，list[int]，≥ 2）
  - `start_date`、`end_date`
- **说明**：多因子 IC/分层收益对比。

### 8.8 `GET /factors/decay-check`
- **说明**：手动触发全量因子衰减检测（IC 滚动窗口告警）。

### 8.9 `GET /factors/{factor_id}/decay`
- **Query**：`max_lag`（默认 20，上限 40）
- **说明**：单因子 IC 衰减曲线（lag 0 → max_lag）。
- **响应**：`{"ok": true, "data": {"lags": [0,1,...], "ic": [...], "rank_ic": [...]}}`

### 8.10 `GET /factors/export`
- **Query**：
  - `category`、`status`
  - `format` —— `csv`（默认，BOM，Excel 友好） 或 `json`
- **说明**：流式下载因子列表。
- **响应**：`text/csv` 或 `application/json`，附带 `Content-Disposition: attachment`。

### 8.11 `POST /factors/auto-import`
- **Query**：
  - `task_id`（必填，挖掘任务 ID）
  - `ic_threshold`（默认 0.03）
- **说明**：把挖掘任务中 IC ≥ 阈值的因子批量入库（`status=verified`）。
- **错误**：`400 TASK_NOT_DONE` / `NO_FACTORS`、`404 NOT_FOUND`

### 8.12 `POST /factors/seed-alpha158`
- **说明**：导入 Alpha158 标准因子集（158 个 qlib 基准因子）。
- **错误**：`400 ALPHA158_SEEDED` —— 已导入过

### 8.13 `GET /factors/{factor_id}/quantile-analysis`
- **Query**：
  - `n_groups`（默认 5，2–10）
  - `start_date`、`end_date`
- **说明**：因子分组（分层）回测，按因子值分 N 组输出各组净值、多空收益、单调性指标。

### 8.14 `POST /factors/{factor_id}/neutralize`
- **Query**：
  - `method` —— `market_cap`（市值） / `industry`（行业+市值） / `both`（等价 industry）
  - `start_date`、`end_date`
- **说明**：对比中性化前后 IC 指标。

### 8.15 `GET /factors/{factor_id}/deep-analysis`
- **Query**：
  - `start_date`、`end_date`
  - `horizon`（默认 5，1–60，调仓天数）
  - `n_groups`（默认 5，2–10）
  - `ic_window`（默认 60，20–250，滚动 IC 窗口）
- **说明**：因子深度分析 = IC 分布/时序/显著性 + horizon 调仓分层净值 + 换手率曲线 + 衰减。CPU 密集，走进程池；结果缓存 1 小时。
- **错误**：`400 INSUFFICIENT_DATA` / `FACTOR_NOT_COMPUTABLE`

---

## 9. 策略（`strategy.py` + `strategy_ext.py`，14 端点）

> 前缀：`/strategies`；所有回测类端点都依赖 qlib。
>
> ⚠️ **路由顺序**：所有**字面量路由**（`/backtest-results`、`/backtest-statuses`、`/compare-backtests`）必须在 `/{strategy_id}` 之前注册，否则会被参数路由优先匹配返回 422。

### 9.1 `GET /strategies`
- **Query**：`status`（默认 `active`）
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [{
      "id": 5, "name": "Momentum Top30",
      "factor_ids": [12, 17, 28],
      "combination_method": "equal_weight",
      "topk": 30, "n_drop": 5, "rebalance_freq": "day",
      "benchmark": "SH000300",
      "description": "...",
      "orthogonalize": 0,
      "status": "active",
      "created_at": "2026-07-01T10:00:00+08:00"
    }],
    "total": 3
  }
}
```

### 9.2 `POST /strategies`
- **Query**：
  - `name`（必填）
  - `factor_ids`（必填，list[int]）
  - `combination_method`（默认 `equal_weight`）
  - `topk`、`n_drop`
  - `rebalance_freq`（默认 `day`）
  - `benchmark`
  - `description`
  - `orthogonalize`（0/1，是否启用因子正交化）
- **错误**：`422 VALIDATION_ERROR` —— 因子列表为空

### 9.3 `GET /strategies/{strategy_id}`
- **响应 200**：策略详情（含关联因子列表）
- **响应 404**：`NOT_FOUND`

### 9.4 `DELETE /strategies/{strategy_id}`
- **说明**：归档（`status=archived`），不真删。

### 9.5 `POST /strategies/{strategy_id}/backtest`
- **Query**：
  - `start_date`、`end_date`
  - `backend` —— `qlib`（默认，工业级） / `self`（自研）
- **说明**：触发回测（**后台执行**）。
- **错误**：`503 QLIB_NOT_AVAILABLE`、`404 NOT_FOUND`

### 9.6 `GET /strategies/{strategy_id}/backtest-status`
- **说明**：轮询当前回测状态机（`idle` / `running` / `completed` / `failed`）。
- **响应**：
```json
{"ok": true, "data": {"strategy_id": 5, "status": "running", "started_at": "...", "error": null}}
```

### 9.7 `GET /strategies/{strategy_id}/backtest-results`
- **Query**：`limit`（默认 20，上限 100）
- **响应**：该策略历次回测结果列表（按时间倒序）。

### 9.8 `GET /strategies/backtest-results`
- **Query**：`limit`（默认 20，上限 100）
- **说明**：全平台最近回测结果（不限定策略，首页用）。

### 9.9 `GET /strategies/backtest-results/{result_id}`
- **响应**：单次回测详情（净值曲线、回测指标、交易记录等）。
- **响应 404**：`NOT_FOUND`

### 9.10 `GET /strategies/backtest-statuses`
- **说明**：批量返回所有策略的回测状态（首页刷新用）。

### 9.11 `POST /strategies/{strategy_id}/param-sweep`
- **Query**：
  - `topk_list`（默认 `[10, 20, 30, 50]`）
  - `rebalance_list`（默认 `["day", "week"]`）
  - `start_date`、`end_date`
- **说明**：参数扫描（**后台执行**，结果写入 `TaskResult`，按轮询接口取回）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "message": "参数扫描已提交（4 x 2 = 8 组合）",
    "strategy_id": 5,
    "topk_list": [10, 20, 30, 50],
    "rebalance_list": ["day", "week"]
  }
}
```

### 9.12 `GET /strategies/{strategy_id}/param-sweep-results`
- **说明**：读取该策略最近的参数扫描任务状态与结果。
- **错误**：`404 NOT_FOUND`

### 9.13 `POST /strategies/compare-backtests`
- **Query**：`result_ids`（list[int]，≥ 2）
- **说明**：跨次回测对比（指标表格 + 净值曲线）。
- **错误**：`422 VALIDATION_ERROR`、`404 NOT_FOUND`

### 9.14 `GET /strategies/backtest-results/{result_id}/trades`
- **说明**：导出单次回测的交易明细（CSV，浏览器直接下载 `backtest_{id}_trades.csv`）。
  - 若回测有交易明细（`metrics.trades`），直接导出。
  - 否则从净值曲线 `nav_curve` 反推每日净值与超额。

### 9.15 `POST /strategies/{strategy_id}/walk-forward`
- **Query**：
  - `train_window`（默认 `730D`，2 年）
  - `test_window`（默认 `180D`，6 月）
  - `step`（默认 `180D`，滚动步长）
  - `topk_list`（默认 `[10, 20, 30, 50]`，候选 topk）
  - `n_drop`（默认 5）
  - `rebalance`（默认 `day`）
- **说明**：Walk-forward 滚动回测 —— 训练窗选最优 topk，测试窗做样本外验证（**后台执行**）。

### 9.16 `GET /strategies/{strategy_id}/walk-forward-results`
- **说明**：读取 Walk-forward 任务结果。

---

## 10. AI 因子挖掘（`mining.py` + `mining_ext.py`，9 端点）

> 前缀：`/mining`；全部依赖 qlib；全部走速率限制（`3/minute`）。

### 10.1 `GET /mining/tasks`
- **Query**：
  - `task_type` —— `llm` / `symbolic` / `automl` / `text`
  - `status` —— `pending` / `running` / `done` / `failed`
  - `limit`（默认 50，上限 200）
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [{
      "id": 8, "type": "llm", "status": "done",
      "params": {"n_candidates": 10, "n_rounds": 1},
      "candidates_generated": 10, "candidates_passed": 3,
      "best_ic": 0.045, "result_factor_ids": [12, 28, 35],
      "error": null,
      "started_at": "...", "finished_at": "...", "created_at": "..."
    }],
    "total": 1
  }
}
```

### 10.2 `GET /mining/tasks/{task_id}`
- **响应 200**：单个任务详情
- **响应 404**：`NOT_FOUND`

### 10.3 `POST /mining/llm`
- **Query**：
  - `n_candidates`（默认取 `mining.llm.candidates_per_run`）
  - `n_rounds`（1–5，默认 1；> 1 启用迭代挖掘 —— 逐轮反馈改进）
- **说明**：启动 LLM 因子挖掘（**后台执行**，并发上限 `task.max_concurrent`）。
- **错误**：`503 QLIB_NOT_AVAILABLE`

### 10.4 `POST /mining/symbolic`
- **说明**：启动符号回归因子挖掘（GP，基于 gplearn 风格），参数走 `mining.symbolic` 配置块。

### 10.5 `POST /mining/automl`
- **Query**：
  - `factor_ids`（list[int]，必填，≥ 1）
  - `method` —— `lightgbm` / `linear`（默认读取配置）
- **说明**：对已有因子做 AutoML 组合（LightGBM/线性）得到复合因子。
- **错误**：`422 VALIDATION_ERROR`

### 10.6 `POST /mining/text`
- **Query**：`codes`（list[str]，可选，默认取 universe 前 30）
- **说明**：文本因子挖掘（研报/新闻舆情 → 因子）。

### 10.7 `GET /mining/templates`
- **说明**：列出所有挖掘模板（封装好的 prompt + 参数组合）。
- **响应**：
```json
{
  "ok": true,
  "data": {
    "items": [
      {"key": "momentum_basic", "name": "基础动量", "description": "...",
       "llm_prompt": "...", "default_n_candidates": 5}
    ]
  }
}
```

### 10.8 `GET /mining/templates/{template_key}`
- **响应 200**：模板详情（含完整 `llm_prompt`）
- **响应 404**：`NOT_FOUND`

### 10.9 `POST /mining/templates/{template_key}/run`
- **Query**：`n_candidates`（默认 5）
- **说明**：用指定模板启动一次 LLM 挖掘。
- **错误**：`503 QLIB_NOT_AVAILABLE`、`404 NOT_FOUND`

---

## 11. 附录：枚举值与错误码

### 11.1 通用枚举

| 字段 | 合法值 |
|------|--------|
| `StockDataStatus.status` | `syncing` / `ready` / `failed` |
| `Factor.status` | `active` / `disabled` / `verified` |
| `Strategy.status` | `active` / `archived` / `backtest_failed` |
| `MiningTask.status` | `pending` / `running` / `done` / `failed` |
| `MiningTask.type` | `llm` / `symbolic` / `automl` / `text` |
| `data_source` | `chenditc` / `akshare` |
| `period`（K 线） | `1d` / `1w` / `1M` |
| `backend`（回测） | `qlib` / `self` |
| `combination_method` | `equal_weight` / `ic_weight` / `auto` / `lightgbm` / `linear` |
| `rebalance_freq` | `day` / `week` / `month` |

### 11.2 错误码速查

| HTTP | code | 含义 | 触发端点 |
|------|------|------|----------|
| 401 | `AUTH_FAILED` | 登录密码错误 | `POST /auth/login` |
| 404 | `NOT_FOUND` | 资源（因子/策略/挖掘任务/文档/模板/回测结果等）不存在 | 多个 |
| 400 | `UNSUPPORTED_INDEX` | 不支持的指数代码 | `GET /market/kline/{index_code}` |
| 400 | `INVALID_FILE` | 不在白名单内的日志文件名 | `GET /logs` |
| 400 | `TASK_NOT_DONE` | 挖掘任务尚未完成 | `POST /factors/auto-import` |
| 400 | `ALPHA158_SEEDED` | Alpha158 已导入过 | `POST /factors/seed-alpha158` |
| 400 | `INSUFFICIENT_DATA` / `FACTOR_NOT_COMPUTABLE` / `NO_DATA` | 因子评价/分析数据不足 | 因子评价/分析相关 |
| 409 | `SYNC_IN_PROGRESS` | 同一 universe 在 10 分钟内已发起同步 | `POST /quant/data/sync` |
| 422 | `VALIDATION_ERROR` | 业务参数校验失败（参数列表为空、数据源非法等） | 多个 |
| 422 | `EXPR_INVALID` | qlib 因子表达式非法 | `POST /factors` |
| 429 | — | 触发速率限制（登录 5/min，挖掘 3/min） | 登录/挖掘 |
| 500 | `CONFIG_ERROR` | 配置写入失败（数据源切换） | `PUT /quant/data/data-source` |
| 500 | `KLINE_ERROR` / `OVERVIEW_ERROR` / `NEUTRALIZE_ERROR` / `DATA_LOAD_ERROR` | 计算阶段异常 | 市场/因子分析端点 |
| 503 | `QLIB_NOT_AVAILABLE` | qlib 未安装/未初始化 | 评价/回测/挖掘/同步类 |

### 11.3 统一配置项

| 路径 | 默认 | 说明 |
|------|------|------|
| `quant.universe` | `csi300` | 股票池 |
| `quant.data_source` | `chenditc` | 数据源 |
| `quant.default_backtest_period.start` | `2020-01-01` | 默认回测起点 |
| `quant.default_backtest_period.end` | `2024-12-31` | 默认回测终点 |
| `mining.llm.candidates_per_run` | `10` | LLM 单轮生成候选数 |
| `mining.symbolic.*` | — | 符号回归 GP 超参 |
| `task.max_concurrent` | `2` | 挖掘并发上限（信号量） |
| `task.task_timeout_seconds` | `300` | 默认挖掘任务超时 |
| `task.timeouts.symbolic` | — | 符号回归专用超时 |
| `task.timeouts.automl` | — | AutoML 专用超时 |
| `task.timeouts.text` | — | 文本挖掘专用超时 |
| `task.timeouts.llm_hard_limit_seconds` | `7200`（2h） | LLM 硬上限（0 表示无限） |
| `auth_enabled` | `false` | 全局鉴权开关（环境变量 `AUTH_ENABLED`） |
| `login_rate_limit` | `5/minute` | 登录限流 |

### 11.4 数据库表

| 表 | 模型 | 关键字段 |
|----|------|----------|
| `factors` | `Factor` | `id, name, expression, category, ic, rank_ic, icir, turnover, status` |
| `strategies` | `Strategy` | `id, name, factor_ids(JSON), combination_method, topk, n_drop, rebalance_freq, benchmark, orthogonalize, status` |
| `backtest_results` | `BacktestResult` | `id, strategy_id, start_date, end_date, annual_return, sharpe, max_drawdown, nav_curve(JSON)` |
| `mining_tasks` | `MiningTask` | `id, type, status, params, candidates_generated, best_ic, result_factor_ids(JSON)` |
| `task_results` | `TaskResult` | `id, strategy_id, task_type("param-sweep"/"walk-forward"), status, payload(JSON), error` |
| `stock_data_status` | `StockDataStatus` | `universe, latest_date, row_count, stock_count, status, last_updated, last_error` |
| `sync_history` | `SyncHistory` | `universe, data_source, status, duration_seconds, version, latest_date` |
| `logs/*` | 文件 | `app.log` / `error.log` / `api.jsonl` / `perf.jsonl` / `audit.jsonl` |

### 11.5 OpenAPI 元信息

- **生成器**：FastAPI 自动生成
- **访问路径**：
  - Swagger UI：`/docs`
  - ReDoc：`/redoc`
  - OpenAPI JSON：`/openapi.json`
- **Tags**：`auth` / `config` / `docs` / `quant-data` / `data-ext` / `market` / `logs` / `factor` / `factor-ext` / `strategy` / `strategy-ext` / `mining` / `mining-ext`
