---
title: 数据层架构
slug: data-layer
order: 1
group: 架构
summary: 数据源、qlib bin + PostgreSQL 双轨存储、同步流程、宏观/财报/外盘广播、校验与修复
---

# QuantLab 数据层技术文档

> 文档版本：v4.0.0 · 最后更新：2026-08-06
> 维护原则：每次数据层代码变更须同步更新本文档对应小节；所有签名以代码为准。

## 这是什么文档

本文档讲"**数据从哪里来、怎么存、怎么用**"：

- 想了解当前数据源与存储 → §1、§2
- 想了解同步流程（全同步链/回填/增量/指数/ETF/财报/外盘）→ §3、§4
- 想了解宏观数据怎么接入和广播 → §5
- 想了解数据校验与一键补齐 → §6
- 想了解 bin 字段契约与同步 worker → §7、§8
- 已知限制与 TODO → §9

---

## 1. 数据源与存储总览

### 1.1 当前架构（2026-08）

```
数据源
  baostock        A股日K全市场回填（主源，一次拉全市场）+ ETF 日K（按日全市场）
  akshare         宏观指标 / 财报摘要 / EOD 增量兜底 / 指数
  东财 datacenter 宏观指标（PMI/CPI/PPI/GDP）
  腾讯            ETF qfq 对齐修正（source=tencent，覆盖现有时间范围）
        │
        ▼
后端服务（backend/app/services/data/）
  sync_worker.py  独立子进程（full/backfill/eod/repair/indices/etf/macro/fundamental）
  baostock_backfill.py   全量回填 → qlib bin + PG
  macro_sync.py          宏观 → PG macro_indicator → 广播 bin
  fundamental_sync.py    财报 → PG financial_indicator → PIT 广播 bin
  external_market.py     外盘隔夜情绪因子 → 广播 bin
  etf_sync.py / index_sync.py   ETF / 指数 → qlib bin + stock_index 注册
        │
        ▼
存储（双轨）
  qlib bin   features/{code}/{field}.day.bin + calendars + instruments
  PostgreSQL stock_daily / etf_daily / macro_indicator / financial_indicator
             / stock_index / stock_basic / stock_industry / trade_calendar / 业务表
        │
        ▼
消费
  因子引擎（$close / $pmi / $roe / $us_sp500_ret ...）· 回测 · 策略库
```

**关键变更历史**：
- 2026-08-03：移除 chenditc / sync_runner / smart_sync / capital_flow_sync / incremental_sync（SQLite 时代产物），改为 baostock 唯一行情源 + PostgreSQL 存储。
- 2026-08-04：接入宏观数据（东财 + akshare → `macro_indicator` 窄表 → 广播写 bin），修复 bin 重建大小写 bug、同步僵尸锁、完整性校验窗口/instruments 问题。
- 2026-08-05：接入财报（akshare → `financial_indicator` 窄表 → PIT forward-fill 广播 `$roe` 等）；分块回填丢数据 bug 修复。
- 2026-08-06：接入外盘隔夜情绪因子（`$us_sp500_ret` 等）；ETF 标的池（baostock 按日全市场 ETF → `etf_daily` + `instruments/etf_all.txt` + `stock_index(type='etf')`）；指数注册表 `stock_index`。

### 1.2 存储双轨制设计

| 存储 | 承载内容 | 访问方式 | 写入入口 |
|------|----------|----------|----------|
| **qlib bin**（float32） | 日频行情字段（19 个 baostock 字段）+ 宏观（pmi/cpi/...）+ 财报（roe/netprofit_yoy/...）+ 外盘（us_sp500_ret/...） | 因子表达式 `$field` | `_sync_stock_bin` / `broadcast_*` |
| **PostgreSQL** | baostock 全字段 `stock_daily`、ETF `etf_daily`、宏观窄表 `macro_indicator`、财报窄表 `financial_indicator`、指数/ETF 注册表 `stock_index`、业务表（factor/strategy/...）、同步元数据 | SQLAlchemy async | `session` |

**设计原则**：
- 能进 qlib bin 的日频数值字段一律进 bin（因子引擎直接 `$` 引用）。
- 需要 PIT 语义（按公告日查询）的数据进 PG（`macro_indicator.available_date` / `financial_indicator.available_date` 即 PIT 日期）。
- 行情源为 baostock；价格存 **qfq 复权价**，`factor` bin 为常量 1.0（qlib 依赖 `$factor` 识别已复权价），`change` 为日收益率、`tradable` 为涨跌停/ST mask，均为派生字段。
- 指数与 ETF 是**独立的 instrument 类**：只写 OHLCV（ETF 另含 volume/amount/change/tradable/factor），无 stock_daily/财报，通过 `stock_index` 表区分，校验/修复时排除。

### 1.3 关键文件职责

| 文件 | 职责 |
|------|------|
| `baostock_backfill.py` | 全量回填主入口：逐交易日拉全市场 → qlib bin + PG `stock_daily` + `instruments/*.txt` + 日历 |
| `baostock_client.py` | baostock 登录单例（线程安全）+ `query_daily_history_k_AStock`（一日全市场）/ `query_daily_history_k_ETF` |
| `eod_incremental.py` | bin 读写基础设施（`_read_bin/_write_bin/_get_calendar/_write_calendar/_pad_bins_to_calendar`）+ EOD 增量同步 |
| `macro_sync.py` | 宏观指标：东财 + akshare 注册表、抓取、入库、forward-fill、广播 |
| `fundamental_sync.py` | 财报：akshare 逐股摘要 → `financial_indicator` 窄表 → PIT forward-fill 广播 |
| `external_market.py` | 外盘隔夜情绪因子：拉取 → 对齐 A股日历 → 广播 bin |
| `index_sync.py` / `index_registry.py` | 主要指数日K → qlib bin；注册表（akshare 拉取 + `seed_indices.py` 回填） |
| `etf_sync.py` | 全市场 ETF 日K → qlib bin + `etf_daily` 窄表 + `etf_all.txt` + `stock_index` 注册 |
| `repair.py` | 一键补齐：按校验差异从 PG 重建 bin（`include_baostock` 可选增量） |
| `validation.py` | 跨存储校验：bin 字段/长度、日历、DB↔bin 覆盖、宏观/财报抽样（index-aware） |
| `full_sync.py` | 一键全同步编排（A股回填 → 指数 → 宏观 → 财报 → 外盘，分阶段进度） |
| `sync_worker.py` | 独立 worker 子进程（kind ∈ full/backfill/eod/repair/indices/etf/macro/fundamental），与 web 进程解耦 |
| `sync_progress.py` | 同步进度（共享文件 + worker PID 存活检测） |
| `sync_lock.py` | 爬取锁（flock），串行化 baostock 爬虫（backfill/eod/indices/repair/full） |

---

## 2. 数据源说明

| 数据源 | 用途 | 状态 |
|--------|------|------|
| **baostock** | A股日K全市场回填（主源）、ETF 日K、EOD 增量 | ✅ 已接入，手动触发 |
| **akshare** | 宏观指标、财报摘要、指数、EOD 增量兜底 | ✅ 已接入 |
| **东财 datacenter** | 宏观指标（PMI/CPI/PPI/GDP） | ✅ 已接入 |
| **腾讯** | ETF qfq 对齐修正（`source=tencent`） | ✅ 已接入 |

> ⚠️ **baostock 风控**：免费接口有登录频率/IP 限制，误触发会返回 `10001011 黑名单用户`。遭遇时需等待（一般数小时到一天），期间不要反复触发登录。纯 PG 重建的 repair/补齐不受影响。

---

## 3. 同步流程

所有同步均为**手动触发**（无自动同步，符合项目惯例）。由 `sync_worker` 独立子进程执行，与 web 进程解耦（uvicorn --reload 重启不影响）。baostock 爬虫类 job（backfill/eod/indices/repair/full）被 `SyncLock` flock 串行化；akshare/东财 job 跳过该锁。

### 3.1 一键全同步（full）

```
POST /api/v1/quant/data/sync-full?years=N&universe=all
```

按依赖顺序串联（bin 必须对齐最终日历 day.txt，否则因子全 NaN）：

```
A股回填（years 年）→ 指数 → 宏观（广播）→ 财报（拉取+广播）→ 外盘
```

- worker：`sync_worker --kind full`（内部按阶段推进，每阶段后重设 worker_pid 防僵尸进度）
- 行为：回填从最新交易日向旧，逐日 `query_daily_history_k_AStock` 拉全市场 → 写 qlib bin（19 字段）+ PG `stock_daily`（baostock 全字段）→ 重建 `instruments/*.txt`
- PG 幂等（`ON CONFLICT DO NOTHING`），重复执行只补缺口
- 约束：≤50k 请求/天、串行（`SyncLock` 防并发）

### 3.2 增量 EOD 同步

```
POST /api/v1/quant/data/eod-sync?universe=csi300&days=5&overwrite=false&source=baostock
GET  /api/v1/quant/data/eod-result
```

- 基于 baostock（主，一次拉全市场）或 akshare（兜底，逐只爬）拉最近 N 天，增量追加到 qlib bin
- 日历增长后调用 `_pad_bins_to_calendar` 对停牌/退市股做 NaN 填充，保持 `4 + 4×len(day.txt)` 长度契约
- `overwrite=true` 会用新数据覆盖已有日期（可能因复权差异导致价格断裂，慎用）
- 真实结果写 `data/eod_last_result.json`，前端轮询 `/eod-result`

### 3.3 指数同步

```
POST /api/v1/quant/data/sync-indices
```

- `index_sync`：akshare 拉主要指数（上证/沪深300/上证50/中证500/中证1000/深证成指/创业板指/科创50）日K 写 bin，自动注册到 `stock_index`（type='index'）
- 指数只写 OHLCV 字段，不落 `stock_daily`/财报

### 3.4 ETF 同步

```
POST /api/v1/quant/data/sync-etf?years=2&overwrite=false&source=baostock
```

- baostock `query_daily_history_k_ETF` 按交易日一次拉全市场 → qlib bin（OHLCV+amount+change+tradable+factor）+ PG `etf_daily` 窄表 + `instruments/etf_all.txt` + `stock_index(type='etf')`
- `source=tencent` 时走腾讯 qfq 对齐现有时间范围（修正复权），资产类别为 ETF 时 qlib 保留 T+1 时序、vbt 按信号日成交

### 3.5 宏观同步

```
POST /api/v1/macro/sync?broadcast=true
```

详见 §5。

### 3.6 财报同步

```
POST /api/v1/quant/data/fundamental/sync?broadcast=true
```

- akshare 逐股财务摘要 → PG `financial_indicator` 窄表（全市场约 5400 次请求）
- `broadcast=true` 时按 `available_date` PIT forward-fill 写 bin（`$roe`/`$netprofit_yoy`/...）
- **部分拉取保护**：某代码只有拥有 ≥ 半数 `FIN_FIELD_NAMES` 字段才记为"已拉取"，否则下次重拉，避免截断响应造成永久缺字段

### 3.7 外盘同步

```
POST /api/v1/quant/data/sync-external-market
```

- 拉取外盘指数（标普/纳指/道指/恒指）→ 对齐 A股日历 → 广播成 `$us_sp500_ret`、`$us_nasdaq_ret` 等 bin 因子字段
- 轻量操作，直接在 web 进程经 `run_io_cpu` 执行；建议每个交易日 A股开盘后手动触发一次

### 3.8 进度查询

```
GET /api/v1/quant/data/sync-progress
```

返回 `{universe, data_source, status, progress_pct, message, worker_pid}`。前端据此显示进度条（数据管理页 / 宏观页）。

---

## 4. 同步 Worker 与进度

### 4.1 独立子进程模型

`sync_worker.py` 以 `subprocess.Popen(start_new_session=True)` 启动，kind ∈ {full, backfill, eod, repair, indices, etf, macro, fundamental}：
- 不占用 web 事件循环
- 独立进程组，web 重启不会杀它
- `SyncLock`（flock）保证 baostock 爬虫同一时刻只有一个

### 4.2 进度与僵尸防护

`sync_progress.py` 用共享文件 `data/sync_progress.json` 桥接 web 与 worker：
- worker `init_progress` → `update_progress` → `finish_progress(ok, error)` 写文件
- web 端 `get_progress()` 读文件（内存为空时回退）
- **僵尸进程识别**：`_pid_alive()` 读 `/proc/<pid>/stat`，状态 `Z`（僵尸）视为已死——避免 worker 崩溃后残留进度文件让 `sync_is_active()` 长期误判"正在同步"（409 卡死）
- **错误透传**：worker 登录/初始化异常（如 baostock 黑名单）时先 `finish_progress(False, error)` 再退出，web 端 `_detect_stale_sync` 读取进度文件真实错误写入 DB，前端直接显示原因而非"[worker 退出]"通用提示

### 4.3 卡死恢复

- 启动时 `recover_stale_sync()`：`status=syncing` 的记录标记 failed（"container restart interrupted sync"）
- 运行时：状态接口触发 `_detect_stale_sync()`——自动触发的超 30 分钟标记 failed；手动触发的检测到 worker 进程已死则立即标记 failed，并把 worker 透传的真实错误写入 `last_error`

---

## 5. 宏观数据专题

### 5.1 架构

```
东财 datacenter（PMI/CPI/PPI/GDP）+ akshare（20 个指标）
   → macro_sync.py 抓取、归一化
   → PG macro_indicator 窄表（PIT）
   → forward_fill 成日频 → 广播写 features/{code}/{field}.day.bin
```

- **窄表模型**：`macro_indicator(indicator, report_date, field_name, value, unit, available_date, source)`，唯一键 `(indicator, report_date, field_name)`
- **PIT 对齐**：`available_date = report_date + delay`（发布延迟防 look-ahead）；月频指标 delay 如 CPI=9、GDP=45 天，日频 delay=0
- **广播**：`forward_fill_to_daily` 按日历 reindex+ffill 成日频，`broadcast_to_all_stocks` 写入所有现存股票（每个宏观字段一份，全市场同值）

### 5.2 指标注册表

`macro_sync.MACRO_INDICATORS`（东财 4 指标 / 5 字段）+ `AKSHARE_INDICATORS`（akshare 20 指标 / 46 字段），合计 **24 指标 / 51 字段**：

| 板块 | 指标 | 字段数 |
|------|------|--------|
| 景气/价格 | PMI(含非制造)、CPI、PPI、GDP（东财） | 5 |
| 利率 | 国债收益率(中债+美债)、Shibor、LPR、回购定盘 FR、银银间回购 FDR | 20 |
| 货币/信贷 | M0/M1/M2、社融、新增贷款、两融 | 9 |
| 商品/汇率 | 商品指数、沪铜、原油、沪金、美元中间价 | 5 |
| 风险/情绪 | 波指 iVIX、股指期货 IF/IC、国债期货 | 7 |

字段名形如 `pmi`/`trsy10y`/`fdr007`/`ivix`，因子表达式用 `$字段名` 引用。

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
| macro/fin | 宏观/财报 bin 字段抽样 |
| qlib | 复用 `check_integrity` 抽样加载（instruments 走 `list_instruments`，窗口取日历实际范围） |

**Index-aware**：校验通过 `stock_index` 表识别指数/ETF 代码并跳过（它们只写 OHLCV，无 stock_daily/财报）。NaN 比例"corrupt"启发式按个股自身上市区间（stock_daily min/max）衡量，避免新上市股误报。

`drift.needs_repair` 为 true 时前端显示"一键补齐"按钮。

### 6.2 一键补齐

```
POST /api/v1/quant/data/repair   { include_baostock, universe }
```

`repair.run_repair`：
1. 重建 day.txt（以 stock_daily 为权威）
2. 对差异股票从 PG `stock_daily` 重建 bin（`_fetch_stock_rows` 已做代码大小写归一化）
3. 重建 instruments；宏观/财报字段重新广播
4. `include_baostock=true` 且 PG 缺交易日时走 baostock 增量
5. 无 `stock_daily` 行的代码记入 `skipped` 并报告（不静默丢弃）

前 3 步不消耗 baostock 配额（纯 PG 重建），baostock 被拉黑时仍可用。

---

## 7. bin 字段契约

### 7.1 行情字段（股票，baostock 全量 19 个）

`features/{code_lower}/{field}.day.bin`，float32，`4 字节 start_index 头 + 数组`，数组与日历对齐：

```
open / high / low / close / preclose / volume / amount / turn /
tradestatus / pct_chg / is_st / pe_ttm / pb_mrq / ps_ttm / pcf_ncf_ttm /
adjustflag / change / tradable / factor
```

- `change`：日收益率（派生）；`tradable`：涨跌停/ST 5% mask（派生）
- **`factor` 恒为 1.0**（派生常量）：价格以 qfq 复权价存储，qlib 依赖 `$factor` 识别为已复权价——它不是 baostock 字段
- 当 `BIN_FIELDS` 变更时，现有 bin 必须重新生成（fresh backfill/repair），否则缺字段检查全市场报错

### 7.2 ETF 字段（9 个）

```
open / high / low / close / volume / amount / change / tradable / factor
```

### 7.3 指数字段

仅 OHLCV（部分含 volume/amount），无 stock_daily/财报。

### 7.4 广播字段（全市场同值）

- 宏观：`pmi / pmi_nm / cpi / ppi / gdp / trsy2y / trsy10y / ... / ivix / fdr007 / ...`（24 指标 / 51 字段，见 §5.2）
- 财报：`roe / netprofit_yoy / ...`（17 字段，PIT forward-fill，`$roe`/`$netprofit_yoy` 等）
- 外盘：`us_sp500_ret / us_nasdaq_ret / ...`（隔夜情绪因子）

### 7.5 日历与 instruments

- `calendars/day.txt`：唯一时间轴（master calendar），bin 数组经 start_index 对齐
- `instruments/{pool}.txt`：`all` / `csiall` / `csi300` / `csi500` / `etf_all`，每行 `code<TAB>start<TAB>end`
- **日历增长规则**：bin 必须恰好 `4 + 4×len(day.txt)` 字节。新交易日加入后，无数据股票（退市/长期停牌）由 `_pad_bins_to_calendar` NaN 填充至新长度，否则校验报"长度异常"。bin 写盘原子化（temp + `os.replace`）；回测/挖掘仅在日历变动型同步（`calendar_shifting_active`）期间阻塞，EOD/ETF 同步不阻塞

---

## 8. PostgreSQL 表

| 表 | 说明 |
|----|------|
| `stock_daily` | baostock 全字段日K（OHLCV/preclose/volume/amount/turn/tradestatus/pct_chg/is_st/pe_ttm/pb_mrq/ps_ttm/pcf_ncf_ttm/adjustflag），键 `(code, trade_date)` |
| `etf_daily` | ETF 窄表（OHLCV），键 `(code, trade_date)` |
| `macro_indicator` | 宏观窄表（PIT），键 `(indicator, report_date, field_name)` |
| `financial_indicator` | 财报窄表，键 `(code, report_date, field_name, value, available_date)`，PIT 语义 |
| `stock_index` | 指数/ETF 注册表（`type` 列 'index'/'etf'），校验/修复据此排除非股票 |
| `stock_basic` / `stock_industry` / `trade_calendar` | 股票基础信息 / 行业 / 交易日历 |
| `news_policy` | 新闻联播文字稿（政策风向页，akshare `news_cctv`；只展示不接 bin），键 `(news_date, title)` |
| `policy_analysis` | 每日 AI 政策解读（LLM 生成：摘要/定调/点名行业/主题热度/关键词），键 `news_date`，`status` done/failed，失败自动重试 |
| 业务表 | `factor` / `strategy` / `backtest_result` / `mining_task` / `task_result` / `user` |
| 同步元数据 | `stock_data_status` / `sync_history` |

---

## 9. 已知限制与 TODO

| # | 模块 | 问题 |
|---|------|------|
| 1 | baostock | 账号/IP 可能被风控拉黑（10001011），需等待解封 |
| 2 | 日历 | 宏观/财报 bin 与日历长度耦合，日历变更后需重新广播（§5.4） |
| 3 | 宏观 | DR007 精确加权平均不可得（akshare 仅 FR007/FDR007 定盘口径） |
| 4 | 北向 | 北向持股明细 2024-08 起停更（港交所披露规则变更）；市场热度已切换为非东财源（乐咕全A估值/股息率/拥挤度、新浪上证指数），`hsgt_*` bin 已随广播清理 |
| 4b | 市场热度 | 拥挤度（`stock_a_congestion_lg`）实际发布滞后约 2 个月，config 内 delay=60 做 PIT 保护；`stock_buffett_index_lg`（巴菲特指标）当前 akshare 版本接口异常暂不可用 |
| 5 | 完整性 | `check_fields` 用 `文件长度==期望` 严格判定，日历被截短时会全市场误报；建议按 start_index 对齐或放宽为 ≥ |
| 6 | AutoML | `factor_eval._resolve_task_id_from_factor_ids()` 仍读旧 SQLite `data/quantlab.db` 映射老格式 `AutoML(method, fid1, ...)` 表达式（SQLite→PG 迁移遗留），文件删除后需改为查 PG |

---

## 附录：文件路径速查

| 模块 | 路径 |
|------|------|
| baostock 回填 | `backend/app/services/data/baostock_backfill.py` |
| baostock 客户端 | `backend/app/services/data/baostock_client.py` |
| bin 基础设施 / EOD | `backend/app/services/data/eod_incremental.py` |
| 宏观同步 | `backend/app/services/data/macro_sync.py` |
| 财报同步 | `backend/app/services/data/fundamental_sync.py` |
| 外盘同步 | `backend/app/services/data/external_market.py` |
| 指数同步 | `backend/app/services/data/index_sync.py` |
| ETF 同步 | `backend/app/services/data/etf_sync.py` |
| 一键全同步编排 | `backend/app/services/data/full_sync.py` |
| 一键补齐 | `backend/app/services/data/repair.py` |
| 数据校验 | `backend/app/services/data/validation.py` |
| 同步 worker | `backend/app/services/data/sync_worker.py` |
| 进度 | `backend/app/services/data/sync_progress.py` |
| 爬取锁 | `backend/app/services/data/sync_lock.py` |
| 宏观 API | `backend/app/api/macro.py` |
| 数据管理 API | `backend/app/api/quant_data.py` + `backend/app/api/data_ext.py` |
| qlib bin | `data/qlib_bin/cn_data/` |
| PostgreSQL | 连接见 `.env` `DATABASE_URL` / `POSTGRES_*` |

*文档版本：v4.0.0 · 最后更新：2026-08-06*
