---
title: 数据层架构
slug: data-layer
order: 1
group: 架构
summary: 数据源、qlib bin + PostgreSQL 双轨存储、同步流程、宏观数据、校验与修复
---

# QuantLab 数据层技术文档

> 文档版本：v3.0.2 · 最后更新：2026-08-04
> 维护原则：每次数据层代码变更须同步更新本文档对应小节；所有签名以代码为准。

## 这是什么文档

本文档讲"**数据从哪里来、怎么存、怎么用**"：

- 想了解当前数据源与存储 → §1、§2
- 想了解同步流程（回填/增量/宏观/指数）→ §3、§4
- 想了解宏观数据怎么接入和广播 → §5
- 想了解数据校验与一键补齐 → §6
- 想了解 bin 字段契约与同步 worker → §7、§8
- 已知限制与 TODO → §9

---

## 1. 数据源与存储总览

### 1.1 当前架构（2026-08）

```
数据源
  baostock        A股日K全市场回填（主源，一次拉全市场）
  akshare         宏观指标 / 新闻 / EOD 增量兜底
  东财 datacenter 宏观指标（PMI/CPI/PPI/GDP）
        │
        ▼
后端服务（backend/app/services/data/）
  baostock_backfill.py   全量回填 → qlib bin + PG
  macro_sync.py          宏观 → PG macro_indicator → 广播 bin
  sync_worker.py         独立子进程（backfill/eod/repair/indices）
        │
        ▼
存储（双轨）
  qlib bin   features/{code}/{field}.day.bin + calendars + instruments
  PostgreSQL stock_daily / macro_indicator / stock_basic / trade_calendar
        │
        ▼
消费
  因子引擎（$close / $pmi ...）· 回测 · 策略库
```

**关键变更历史**：
- 2026-08-03：移除 chenditc / sync_runner / smart_sync / fundamental_sync / capital_flow_sync / incremental_sync（SQLite 时代产物），改为 baostock 唯一行情源 + PostgreSQL 存储。
- 2026-08-04：接入宏观数据（东财 + akshare → `macro_indicator` 窄表 → 广播写 bin），修复 bin 重建大小写 bug、同步僵尸锁、完整性校验窗口/insturments 问题。

### 1.2 存储双轨制设计

| 存储 | 承载内容 | 访问方式 | 写入入口 |
|------|----------|----------|----------|
| **qlib bin**（float32） | 日频行情字段（OHLCV/amount/change/tradable）+ 宏观字段（pmi/cpi/...） | 因子表达式 `$field` | `_write_bin` |
| **PostgreSQL** | baostock 全字段 `stock_daily`、宏观窄表 `macro_indicator`、业务表（factor/strategy/...）、同步元数据 | SQLAlchemy async | `session` |

**设计原则**：
- 能进 qlib bin 的日频数值字段一律进 bin（因子引擎直接 `$` 引用）。
- 需要 PIT 语义（按公告日查询）的数据进 PG（`macro_indicator.available_date` 即 PIT 日期）。
- 行情源为 baostock，不再有复权字段（`vwap`/`adjclose`/`factor` 不再存储，baostock 不提供）。

### 1.3 关键文件职责

| 文件 | 职责 |
|------|------|
| `baostock_backfill.py` | 全量回填主入口：逐交易日拉全市场 → qlib bin + PG `stock_daily` + `instruments/*.txt` + 日历 |
| `baostock_client.py` | baostock 登录单例（线程安全）+ `query_daily_history_k_AStock`（一日全市场） |
| `eod_incremental.py` | bin 读写基础设施（`_read_bin/_write_bin/_get_calendar/_write_calendar`）+ EOD 增量同步 |
| `macro_sync.py` | 宏观指标：东财 + akshare 注册表、抓取、入库、forward-fill、广播 |
| `repair.py` | 一键补齐：按校验差异从 PG 重建 bin |
| `validation.py` | 跨存储校验：bin 字段/字段集合/日历/覆盖/qlib 可读性 |
| `integrity_check.py` | qlib 端到端加载冒烟检查 |
| `sync_worker.py` | 独立 worker 子进程（backfill/eod/repair/indices），与 web 进程解耦 |
| `sync_progress.py` | 同步进度（共享文件 + worker PID 存活检测） |
| `sync_lock.py` | 爬取锁（flock），防止并发连 baostock |
| `index_sync.py` | 主要指数日K → qlib bin |

---

## 2. 数据源说明

| 数据源 | 用途 | 状态 |
|--------|------|------|
| **baostock** | A股日K全市场回填（主源） | ✅ 已接入，手动触发 |
| **akshare** | 宏观指标、新闻、EOD 增量兜底 | ✅ 已接入 |
| **东财 datacenter** | 宏观指标（PMI/CPI/PPI/GDP） | ✅ 已接入 |

> ⚠️ **baostock 风控**：免费接口有登录频率/IP 限制，误触发会返回 `10001011 黑名单用户`。遭遇时需等待（一般数小时到一天），期间不要反复触发登录。纯 PG 重建的 repair/补齐不受影响。

---

## 3. 同步流程

所有同步均为**手动触发**（无自动同步，符合项目惯例）。由 `sync_worker` 独立子进程执行，与 web 进程解耦（uvicorn --reload 重启不影响）。

### 3.1 全量回填（baostock）

```
POST /api/v1/quant/data/sync?years=N
```

- worker：`sync_worker --kind backfill`
- 行为：从最新交易日向旧，逐日 `query_daily_history_k_AStock` 拉全市场 → 写 qlib bin（OHLCV/amount/change/tradable）+ PG `stock_daily`（baostock 全字段）→ 重建 `instruments/*.txt`
- PG 幂等（`ON CONFLICT DO NOTHING`），重复执行只补缺口
- 约束：≤50k 请求/天、串行（`SyncLock` 防并发）

### 3.2 增量 EOD 同步

```
POST /api/v1/quant/data/eod-sync   { universe, days, overwrite, source }
```

- 基于 baostock（主）或 akshare（兜底）拉最近 N 天，增量追加到 qlib bin
- `overwrite=true` 会用新数据覆盖已有日期（可能因复权差异导致价格断裂，慎用）

### 3.3 指数同步

```
POST /api/v1/quant/data/sync-indices
```

- `index_sync.sync_indices_to_qlib`：拉 8 大指数日K写 bin（上证/沪深300/上证50/中证500/中证1000/深证成指/创业板指/科创50）

### 3.4 宏观同步

```
POST /api/v1/macro/sync
```

详见 §5。

### 3.5 进度查询

```
GET /api/v1/quant/data/sync-progress
```

返回 `{universe, data_source, status, progress_pct, message, worker_pid}`。前端据此显示进度条（数据管理页 / 宏观页）。

---

## 4. 同步 Worker 与进度

### 4.1 独立子进程模型

`sync_worker.py` 以 `subprocess.Popen(start_new_session=True)` 启动，kind ∈ {backfill, eod, repair, indices}：
- 不占用 web 事件循环
- 独立进程组，web 重启不会杀它
- `SyncLock`（flock）保证同一时刻只有一个爬取进程

### 4.2 进度与僵尸防护

`sync_progress.py` 用共享文件 `data/sync_progress.json` 桥接 web 与 worker：
- worker `init_progress` → `update_progress` → `finish_progress(ok, error)` 写文件
- web 端 `get_progress()` 读文件（内存为空时回退）
- **僵尸进程识别**：`_pid_alive()` 读 `/proc/<pid>/stat`，状态 `Z`（僵尸）视为已死——避免 worker 崩溃后残留进度文件让 `sync_is_active()` 长期误判"正在同步"（409 卡死）

**错误透传**：worker 登录/初始化异常（如 baostock 黑名单）时先 `finish_progress(False, error)` 再退出，web 端 `_detect_stale_sync` 会读取进度文件真实错误写入 DB，前端直接显示原因而非"[worker 退出]"通用提示。

### 4.3 卡死恢复

- 启动时 `recover_stale_sync()`：`status=syncing` 的记录标记 failed（"container restart interrupted sync"）
- 运行时：状态接口触发 `_detect_stale_sync()`，检测到 worker 已死且超时未完成则标记 failed

---

## 5. 宏观数据专题

### 5.1 架构

```
东财 datacenter（PMI/CPI/PPI/GDP）+ akshare（19 个指标）
   → macro_sync.py 抓取、归一化
   → PG macro_indicator 窄表（PIT）
   → forward_fill 成日频 → 广播写 features/{code}/{field}.day.bin
```

- **窄表模型**：`macro_indicator(indicator, report_date, field_name, value, unit, available_date, source)`，唯一键 `(indicator, report_date, field_name)`
- **PIT 对齐**：`available_date = report_date + delay`（发布延迟防 look-ahead）；月频指标 delay 如 CPI=9、GDP=45 天，日频 delay=0
- **广播**：`forward_fill_to_daily` 按日历 reindex+ffill 成日频，`broadcast_to_all_stocks` 写入所有现存股票（每个宏观字段一份，全市场同值）

### 5.2 指标注册表

`macro_sync.MACRO_INDICATORS`（东财 4 指标 / 5 字段）+ `AKSHARE_INDICATORS`（akshare 19 指标 / 40 字段）：

| 板块 | 指标 | 字段数 |
|------|------|--------|
| 景气/价格 | PMI(含非制造)、CPI、PPI、GDP | 5 |
| 利率 | 国债收益率(中债+美债 8)、Shibor(4)、LPR(2)、回购定盘 FR(3)、银银间回购 FDR(3) | 20 |
| 货币/信贷 | M0/M1/M2(3)、社融(2)、新增贷款(2)、两融(沪/深 2) | 9 |
| 商品/汇率 | 商品指数、沪铜、原油、沪金、美元中间价 | 5 |
| 风险/情绪 | 波指 iVIX、股指期货 IF/IC(各 2)、国债期货 | 6 |

合计 **23 指标 / 45 字段**，字段名形如 `pmi`/`trsy10y`/`fdr007`/`ivix`，因子表达式用 `$字段名` 引用。

> 注：REPO_FR/FDR 走 akshare `repo_rate_query`（Chinamoney CSV 全量历史）；`repo_rate_hist` 传日期范围会触发 akshare bug，勿用。

### 5.3 单位换算

字段配置支持 `scale`（如两融余额源单位为"元"，`scale=1e-8` 存为"亿元"），避免大数展示困难。

### 5.4 已知坑：日历长度耦合

宏观 bin 数组长度 = **广播时**的 day.txt 长度。任何改变日历长度的操作（全量回填/EOD）之后，**必须重新触发宏观同步**重广播，否则宏观 bin 与日历错位（qlib 读不到）。长期方案：宏观数据留在 PG 权威源，因子评估时按 `available_date` merge，彻底解耦日历（见 CODEBUDDY.md 演进方向）。

---

## 6. 数据校验与一键补齐

### 6.1 校验

```
GET /api/v1/quant/data/validate?universe=all
```

`validation.run_validation` 返回结构化报告（`checks` + `drift`）：

| 检查项 | 内容 |
|--------|------|
| fields | 全市场 bin 字段完整性与长度（期望长度 = 4 + 4×日历天数） |
| fieldset | stock_daily 列 ⊆ bin 字段 |
| calendar | day.txt vs stock_daily vs trade_calendar |
| coverage | 每股数据区间（DB vs bin） |
| qlib | 复用 `check_integrity` 抽样加载（instruments 走 `list_instruments`，窗口取日历实际范围） |

`drift.needs_repair` 为 true 时前端显示"一键补齐"按钮。

### 6.2 一键补齐

```
POST /api/v1/quant/data/repair   { include_baostock, universe }
```

`repair.run_repair`：
1. 重建 day.txt（以 stock_daily 为权威）
2. 对差异股票从 PG `stock_daily` 重建 bin（`_fetch_stock_rows` 已做代码大小写归一化）
3. 重建 instruments
4. `include_baostock=true` 且 PG 缺交易日时走 baostock 增量

前 3 步不消耗 baostock 配额（纯 PG 重建），baostock 被拉黑时仍可用。

---

## 7. bin 字段契约

### 7.1 行情字段（baostock 子集）

`features/{code_lower}/{field}.day.bin`，float32，`4 字节 start_index 头 + 数组`，数组与日历对齐：

`open / high / low / close / volume / amount / change（涨跌幅） / tradable（涨跌停/ST mask）`

> **不再存储** `vwap / adjclose / factor`（baostock 不提供）。

### 7.2 宏观字段

`pmi / pmi_nm / cpi / ppi / gdp / trsy2y / trsy10y / ... / ivix / fdr007 / ...`（见 §5.2），全市场同值广播。

### 7.3 日历与 instruments

- `calendars/day.txt`：唯一时间轴（master calendar），bin 数组经 start_index 对齐
- `instruments/{pool}.txt`：`all` / `csiall`（可扩展 csi300/csi500），每行 `code<TAB>start<TAB>end`

---

## 8. PostgreSQL 表

| 表 | 说明 |
|----|------|
| `stock_daily` | baostock 全字段日K（OHLCV/preclose/volume/amount/turn/tradestatus/pct_chg/is_st/pe_ttm/pb_mrq/ps_ttm/pcf_ncf_ttm/adjustflag），键 `(code, trade_date)` |
| `macro_indicator` | 宏观窄表（PIT），键 `(indicator, report_date, field_name)` |
| `stock_basic` / `stock_industry` / `trade_calendar` | 股票基础信息 / 行业 / 交易日历 |
| `fin_profit` 等 7 表 / `margin_daily` | **schema-only，尚未回填**（baostock 逐股逐季请求成本高） |
| 业务表 | `factor` / `strategy` / `backtest_result` / `mining_task` / `task_result` / `user` |
| 同步元数据 | `stock_data_status` / `sync_history` |

---

## 9. 已知限制与 TODO

| # | 模块 | 问题 |
|---|------|------|
| 1 | baostock | 账号/IP 可能被风控拉黑（10001011），需等待解封 |
| 2 | 日历 | 当前 `day.txt` 仅 22 天（2026-07-03 ~ 08-03），全量回填被中断；规则策略/长窗口因子需等回填 |
| 3 | trade_calendar | `trade_calendar` 表为空（baostock 官方交易日历未入库），"日历同步"无法与官方核对 |
| 4 | 宏观 | 宏观 bin 与日历长度耦合，日历变更后需重新广播（§5.4） |
| 5 | 宏观 | DR007 精确加权平均不可得（akshare 仅 FR007/FDR007 定盘口径） |
| 6 | 财务 | `fin_*` 财报表 schema-only，未回填 |
| 7 | 北向 | 北向持股明细 2024-08 起停更（港交所披露规则变更） |
| 8 | 完整性 | `check_fields` 用 `文件长度==期望` 严格判定，日历被截短时会全市场误报；建议按 start_index 对齐或放宽为 ≥ |

---

## 附录：文件路径速查

| 模块 | 路径 |
|------|------|
| baostock 回填 | `backend/app/services/data/baostock_backfill.py` |
| baostock 客户端 | `backend/app/services/data/baostock_client.py` |
| bin 基础设施 / EOD | `backend/app/services/data/eod_incremental.py` |
| 宏观同步 | `backend/app/services/data/macro_sync.py` |
| 一键补齐 | `backend/app/services/data/repair.py` |
| 数据校验 | `backend/app/services/data/validation.py` |
| 完整性冒烟 | `backend/app/services/data/integrity_check.py` |
| 同步 worker | `backend/app/services/data/sync_worker.py` |
| 进度 | `backend/app/services/data/sync_progress.py` |
| 爬取锁 | `backend/app/services/data/sync_lock.py` |
| 指数同步 | `backend/app/services/data/index_sync.py` |
| 宏观 API | `backend/app/api/macro.py` |
| qlib bin | `data/qlib_bin/cn_data/` |
| PostgreSQL | 连接见 `.env` `DATABASE_URL` / `POSTGRES_*` |

*文档版本：v3.0.2 · 最后更新：2026-08-04*
