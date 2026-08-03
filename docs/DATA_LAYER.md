---
title: 数据层架构
slug: data-layer
order: 1
group: 架构
summary: 数据采集、QLib bin 双轨存储、涨跌停mask、基本面PIT、资金情绪采集
---

# QuantLab 数据层技术文档

> 文档版本：v3.0.2 · 最后更新：2026-08-02
> 适用范围：QuantLab 阶段1数据层改造 + 后续增强（增量同步、资金情绪、归档）
> 文档目的：记录数据层架构、每个文件/函数/参数说明、数据源调度、部署注意事项
> 维护原则：每次数据层代码变更须同步更新本文档对应小节；所有签名以代码为准

> ⚠️ **过时声明（2026-08-03）**：本文档大篇幅描述的是**已被移除的历史架构**。当前数据层以 baostock 为唯一行情源：
> - 主同步入口为 `backend/app/services/data/baostock_backfill.py`（`POST /api/v1/quant/data/sync?years=N`，手动触发，从最新向旧逐交易日拉全市场）
> - 已删除：`sync_runner.py`、`chenditc_client.py`、`incremental_sync.py`、`smart_sync.py`、`fundamental_sync.py`、`capital_flow_sync.py`
> - 关系存储为 **PostgreSQL**（非 SQLite）；qlib bin + PG `stock_daily` 双轨
> - akshare 仅作补充源（`akshare_client.py`：新闻/市值/行业/EOD 增量兜底）
> **本文档下述架构图、采集器矩阵、调度表、SQLite 章节均为历史记录，请以代码为准。**

---

## 这是什么文档

本文档专门讲"**数据从哪里来、怎么存、怎么用**"。如果你：

- 想了解 baostock/AKShare/chenditc qlib bin 三套数据源的取舍 → §1
- 想调增量同步、断点续传 → §2、§3
- 想加新字段（资金/情绪/估值）→ §4
- 想了解涨跌停 mask 与 PIT 基本面 → §5、§6
- 想了解数据库归档和清理策略 → §7

---

## 1. 数据层架构概览

### 1.1 整体分层与数据流

```
┌──────────────────────────────────────────────────────────────────┐
│  数据源层                                                         │
│   ├─ chenditc/investment_data  预构建 qlib_bin.tar.gz（推荐主源） │
│   ├─ akshare                   日K/估值/资金/龙虎榜/指数（兜底）  │
│   └─ mootdx / tushare          TODO fallback                     │
└───────────────┬──────────────────────────────────────────────────┘
                │  config.quant.data_source 决定走哪条
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  调度入口                                                         │
│   sync_runner.run_sync_task(req)                                 │
│     ├─ data_source=chenditc → _sync_via_chenditc                 │
│     │     download_qlib_bin / download_and_merge_incremental     │
│     └─ data_source=akshare  → _sync_via_akshare                  │
│           data_adapter.sync_to_qlib(start,end,codes)             │
└───────────────┬──────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  采集器层  backend/app/services/data/                            │
│   ├─ eod_incremental.py    日K OHLCV + $tradable mask（akshare） │
│   ├─ fundamental_sync.py   基本面PIT（估值日频 + 财务报表TODO）  │
│   ├─ capital_flow_sync.py  资金/情绪（北向/龙虎榜/融资融券/大单）│
│   ├─ market_data.py        市值数据 total_mv                     │
│   ├─ industry_sync.py      申万行业 → data/industry_map.json     │
│   ├─ index_sync.py         主要指数日K → qlib bin                │
│   └─ integrity_check.py    bin 文件长度 vs 日历一致性校验        │
└───────────────┬──────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────┬───────────────────────────────────┐
│  存储层（双轨制）             │                                   │
│  QLib bin（float32）         │  SQLite（关系表）                 │
│   features/{code}/{f}.day.bin│   fundamental_pit（PIT 宽表）     │
│   calendars/day.txt          │   stock_data_status / sync_history│
│   instruments/{pool}.txt     │   factor / strategy / mining_task │
└───────────────┬──────────────┴───────────────────────────────────┘
                ▼
        因子引擎（QLib 表达式 $close / $tradable / $north_net ...）
```

### 1.2 存储双轨制设计

| 存储介质 | 承载内容 | 访问方式 | 写入入口 |
|----------|----------|----------|----------|
| QLib bin（float32） | 量价 OHLCV、`$tradable` mask、资金/情绪日频字段、市值 | QLib 表达式 `$field` | `_write_bin` |
| SQLite（关系表） | 基本面 PIT 宽表（含 announce_date）、同步状态、因子/策略 | SQLAlchemy `session` | `session.add` |

**设计原则**：
- 能进 QLib bin 的日频数值字段一律进 bin（因子引擎直接 `$` 引用，零序列化开销）
- 需要 PIT 语义（按公告日查询）的字段进 SQLite，避免 bin 无法表达"版本"概念
- 量价字段走后复权对齐；资金/情绪字段非价格，**不走复权对齐**

### 1.3 采集器与文件职责矩阵

| 文件 | 职责 | 主要产物 | 状态 |
|------|------|----------|------|
| `eod_incremental.py` | 日K增量同步 + 涨跌停mask | OHLCV bin + `tradable.day.bin` | ✅ 已实现 |
| `fundamental_sync.py` | 基本面PIT采集 | `fundamental_pit` 表 | 🟡 估值已实现，财报TODO |
| `capital_flow_sync.py` | 资金/情绪采集 | 4个资金QLib bin字段 | 🟡 沪市融资融券TODO |
| `market_data.py` | 市值数据 | total_mv bin | ✅ 已实现 |
| `industry_sync.py` | 申万行业分类 | `data/industry_map.json` + industry bin | ✅ 已实现 |
| `index_sync.py` | 主要指数日K | 指数 qlib bin | ✅ 已实现 |
| `chenditc_client.py` | chenditc 全量/增量包下载解压 | qlib_bin 目录 | ✅ 已实现 |
| `incremental_sync.py` | chenditc 增量包合并 | 合并到 qlib_bin | ✅ 已实现 |
| `sync_runner.py` | 同步任务总调度 + 错误分类 | StockDataStatus/SyncHistory | ✅ 已实现 |
| `integrity_check.py` | bin 完整性校验 | 缺失/长度异常清单 | ✅ 已实现 |

### 1.4 数据源调度（`sync_runner.py`）

`run_sync_task(req)` 按 `settings.quant.data_source` 分发：

| data_source | 走法 | 函数 | 说明 |
|-------------|------|------|------|
| `chenditc` | 全量/增量包 | `_sync_via_chenditc` → `download_qlib_bin` / `download_and_merge_incremental` | 推荐主源，下载预构建 bin 包 |
| `akshare` | 逐只爬取 | `_sync_via_akshare` → `data_adapter.sync_to_qlib` | 兜底，易被反爬 |

> ⚠️ **配置与默认值差异**：`config.yaml` 当前 `data_source: akshare`；而代码 `get_data_source_api` 与 `daily_quant_data_update` 的兜底默认值为 `chenditc`。切换数据源请用 `PUT /api/v1/quant/data/data-source?source=chenditc`，会回写 `config.yaml` 并更新运行时 `settings.quant`。

---

## 2. 涨跌停 mask 契约（`$tradable` 字段）

### 2.1 设计原理

A股涨跌停日收盘价不可执行：涨停买不进、跌停卖不出。传统回测在因子计算阶段用滚动窗口（MA / Corr / Rank）会读到不可执行价格，导致 **IC 虚高 18%、Sharpe 虚高 0.44**（参考 arxiv 2507.07107）。

本方案在**数据加载层根治**：为每只股票生成 `tradable.day.bin` 字段，让 Mask 算子与回测层过滤都能直接消费。

### 2.2 字段定义

| 属性 | 值 |
|------|----|
| 字段名 | `tradable` |
| 类型 | float32（QLib bin 标准） |
| 取值 | `1.0` = 可交易；`0.0` = 触及涨跌停不可交易 |
| 存储路径 | `{provider_uri}/features/{code_lower}/tradable.day.bin` |
| 频率 | 日频（与 OHLCV 同频） |
| 对齐 | 与 `$close` 同日历对齐，由 `_get_calendar` 保证 |

### 2.3 涨跌停比例（`_get_limit_pct` 函数）

**函数签名**：
```python
def _get_limit_pct(qlib_code: str) -> float:
    """根据证券代码前缀返回涨跌停比例（小数）。"""
```

**板块规则**：

| 板块 | 代码前缀 | 涨跌停比例 | 返回值 |
|------|---------|-----------|--------|
| 主板 | 60 / 00 | 10% | 0.10 |
| 科创板 | 688 | 20% | 0.20 |
| 创业板 | 300 / 301 | 20% | 0.20 |
| 北交所 | 83 / 87 / 43 / 92 / 88 | 30% | 0.30 |

> ⚠️ **已知缺口**：ST 股 5% 涨跌停**未处理**。akshare 无稳定 ST 字段，需后续补 ST 标记表（见 §2.6）。

**实现要点**：
- 入参 `qlib_code` 是 qlib 风格代码（如 `SH600000`、`SZ300750`、`BJ830799`）
- 取字母后的数字部分前缀匹配，前缀命中即返回对应比例
- 未命中任何前缀时返回默认 `0.10`（保守按主板处理）

### 2.4 计算逻辑（`_compute_tradable` 函数）

**函数签名**：
```python
def _compute_tradable(df: pd.DataFrame, qlib_code: str) -> pd.DataFrame:
    """返回含 tradable 列的 DataFrame：1.0 可交易，0.0 触及涨跌停。"""
```

**判定优先级**：

1. **优先用东财源 `pct_change`（涨跌幅%）字段**
   - 判定式：`abs(pct_change) >= limit_pct * 100 - 0.01 → 0.0`
   - 减 `0.01` 是为吸收浮点误差（如 9.99% 实际触及涨停）
   - 东财源字段稳定、精度高，首选

2. **无 `pct_change` 时（新浪源回退）用 `close.pct_change()` 近似**
   - 用前一日收盘计算当日涨跌幅
   - **已知风险**：除权日会算出虚假大幅波动，可能误判为涨跌停
   - 缓解理由：除权日极少同时触及涨跌停，影响样本量可忽略
   - 若后续需精确，应接入 `ak.stock_dividend` 做除权日标记后跳过判定

**输出**：
- 返回 `pd.Series(dtype=float32)`，index 与 df 对齐
- 默认全 `1.0`，仅触及涨跌停日置 `0.0`

### 2.5 因子表达式中的使用

```python
# 方式1：直接用 Mask 算子过滤（推荐，因子层根治）
factor = Mask($close, $tradable)

# 方式2：回测层过滤（已有 only_tradable=True）
# QLib TopkDropoutStrategy 的 only_tradable 参数会过滤不可交易标的
```

**两种方式的关系**：
- 方式1 在因子计算阶段就把不可执行价置 NaN/0，IC/Sharpe 统计即被矫正
- 方式2 在下单阶段过滤，但因子已读到脏价格，统计仍虚高
- **生产建议**：两者同时启用，方式1 矫正统计、方式2 防止实盘下单到涨跌停

### 2.6 手动修改指引

| 需求 | 改动位置 | 改动方式 |
|------|----------|----------|
| 新增板块涨跌停规则 | `_get_limit_pct` | 按代码前缀加 `if/elif` 分支，返回对应比例 |
| 处理 ST 股 5% | 新增 ST 状态表 + `_compute_tradable` | 建 `st_status(code, date, is_st)` 表，判定时若 `is_st=True` 用 `0.05` 阈值 |
| 切换涨跌幅数据源 | `_compute_tradable` 判定分支 | 改 `pct_change` 来源字段名或回退逻辑 |
| 历史 `tradable` 回填 | `incremental_sync_eod` | 跑一次 `incremental_sync_eod(overwrite=True)` 全量回填，或写专门历史回填脚本（当前仅增量同步时生成） |
| 调整浮点吸收阈值 | `_compute_tradable` | 修改 `limit_pct * 100 - 0.01` 中的 `0.01` |

---

## 3. 基本面 PIT 数据（`fundamental_sync.py`）

### 3.1 数据模型（`FundamentalPIT`）

**表名**：`fundamental_pit`

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | String | qlib 代码，如 `SH600000` |
| `report_date` | String | 报告期 `YYYY-MM-DD`（如 `2024-06-30`） |
| `announce_date` | String | **公告日（PIT 关键字段，查询按此过滤）** |
| `revenue` | Float | 营业收入（元） |
| `net_profit` | Float | 归母净利润（元） |
| `total_assets` | Float | 总资产（元） |
| `net_assets` | Float | 净资产（元） |
| `eps` | Float | 每股收益 |
| `bps` | Float | 每股净资产 |
| `roe` | Float | 净资产收益率（%） |
| `pe` / `pb` / `ps` | Float | 估值指标（日频快照） |
| `total_mv` | Float | 总市值（元） |
| `source` | String | 数据源（如 `akshare:stock_a_indicator_lg`） |
| `fetched_at` | TIMESTAMP | 采集时间 |

**索引**：
- `idx_fund_code_date(code, announce_date)` —— PIT 查询主索引
- `idx_fund_report(code, report_date)` —— 按报告期检索

### 3.2 PIT 查询原则

查询必须按 `announce_date <= 交易日` 过滤，取最近版本，杜绝未来函数：

```python
from app.services.data.fundamental_sync import query_fundamental_pit

# 异步调用：返回 2024-06-30 当日可知的最新 PE
# （即 announce_date <= 2024-06-30 的最新记录）
pe = await query_fundamental_pit("SH600000", "2024-06-30", field="pe")
```

**函数签名**：
```python
async def query_fundamental_pit(
    code: str,            # qlib 代码
    trade_date: str,      # 交易日 YYYY-MM-DD
    field: str = "pe",    # 取值字段
) -> Optional[float]:
```

**查询语义**：
- `WHERE code = ? AND announce_date <= ? ORDER BY announce_date DESC LIMIT 1`
- 若该日尚无任何已公告记录，返回 `None`
- **严禁**用 `report_date` 过滤——财报报告期早于公告日，会引入未来函数

### 3.3 采集函数（`sync_fundamental_pit`）

**函数签名**：
```python
async def sync_fundamental_pit(
    codes: list[str],     # qlib 代码列表
    start: str,           # 起始日期 YYYY-MM-DD
    end: str,             # 结束日期 YYYY-MM-DD
) -> None:
```

**当前状态**：
- ✅ 估值日频（PE / PB / PS / 总市值）已实现
- 🟡 财务报表（revenue / net_profit / total_assets / net_assets / eps / bps / roe）解析留 TODO

**数据源**：

| 用途 | akshare 接口 | 状态 |
|------|-------------|------|
| 估值日频 | `stock_a_indicator_lg` | ✅ 已接入 |
| 财务报表 | `stock_financial_abstract` | 🟡 TODO，需 pivot 多级表头 |

**采集流程**：
1. 遍历 `codes`
2. 调 `fetch_valuation_daily(code, start, end)` 拉估值
3. （TODO）调 `fetch_financial_abstract(code)` 拉财报
4. 构造 `FundamentalPIT` records，`session.add_all` 后 commit
5. 估值快照 `announce_date = trade_date`（当日即公告日）

### 3.4 手动修改指引

| 需求 | 改动位置 | 改动方式 |
|------|----------|----------|
| 新增估值字段 | `FundamentalPIT` 模型 + `fetch_valuation_daily` rename 映射 + `sync_fundamental_pit` records 构造 | 三处同步加字段；模型改后须跑 alembic autogenerate（见 §6） |
| 实现财务报表解析 | `fetch_financial_abstract` | 实现 akshare 返回的多级表头 pivot，映射到 `revenue`/`net_profit` 等字段；`announce_date` 取自财报披露日列 |
| 估值与财报拆表 | 新建 `valuation_daily(code, date 唯一)` 与 `financial_report_pit(code, report_date, announce_date)` | 当前混表便于快速查询，数据量增大后再拆；迁移时注意同步 `query_fundamental_pit` 的路由 |
| 调整 PIT 查询字段 | `query_fundamental_pit` 的 `field` 参数 | 模型字段即合法 field 值，无需额外白名单 |

---

## 4. 资金 / 情绪数据（`capital_flow_sync.py`）

### 4.1 四个 QLib bin 字段

| 字段名 | 含义 | akshare 接口 | 已知风险 |
|--------|------|-------------|---------|
| `$north_net` | 北向资金个股净买入额（元） | `stock_hsgt_individual_em` | 净额用持股市值差分计算 |
| `$margin_balance` | 融资融券余额（元） | `stock_margin_detail_szse` | **沪市暂返回空（TODO）** |
| `$dragon_net` | 龙虎榜净买入额（元，仅上榜日） | `stock_lhb_detail_em` | 全市场查询后过滤，效率低 |
| `$big_order_net` | 大单净流入额（元） | `stock_individual_fund_flow` | 北交所未覆盖 |

**字段存储约定**：
- 路径：`{provider_uri}/features/{code_lower}/{field}.day.bin`
- 类型：float32
- 频率：日频
- 缺失日（如未上榜日 `$dragon_net`）填 `0.0`，不填 NaN（避免 QLib 表达式传播 NaN）

### 4.2 采集函数（`sync_capital_flow`）

**函数签名**：
```python
async def sync_capital_flow(
    codes: list[str],                      # qlib 代码列表
    start: str,                            # 起始日期
    end: str,                              # 结束日期
    fields: tuple[str, ...] = CAPITAL_FIELDS,  # 默认全部4个
    overwrite: bool = False,               # 是否覆盖已有 bin
) -> None:
```

**关键设计**：
- **复用 `eod_incremental` 的基础设施**：`_read_bin` / `_write_bin` / `_get_calendar` 等，避免重复实现
- **资金字段不走复权对齐**：资金/情绪非价格序列，不做后复权处理
- **字段分发**：在主入口按 `fields` 中的每一项调对应 `fetch_xxx`

**`CAPITAL_FIELDS` 常量**：
```python
CAPITAL_FIELDS = ("north_net", "margin_balance", "dragon_net", "big_order_net")
```

**底层拉取函数签名**：

| 函数 | 签名要点 | 说明 |
|------|---------|------|
| `fetch_north(code, start, end)` | 北向个股净买入 | `stock_hsgt_individual_em` |
| `fetch_margin(code, start, end)` | 融资融券余额 | 沪市 TODO（`stock_margin_detail_szse` 仅深市） |
| `fetch_dragon(code, start, end)` | 龙虎榜净买入 | `stock_lhb_detail_em`，字段名需运行时验证 |
| `fetch_big_order(code, start, end)` | 大单净流入 | `stock_individual_fund_flow`，北交所 market 未验证 |

### 4.3 因子表达式中的使用

```python
# 北向资金动量因子（20日均值除以收盘价）
factor = Mean($north_net, 20) / $close

# 龙虎榜信号（上榜且净买入为正）
factor = If($dragon_net > 0, 1, 0)

# 大单净流入占比（相对成交额）
factor = $big_order_net / ($volume * $close)
```

**注意事项**：
- `$dragon_net` 大部分日为 `0.0`，用 `If` 而非直接做除法分母
- 资金字段量纲为元，与价格相除前注意单位（建议先 `Log` 或归一化）

### 4.4 手动修改指引

| 需求 | 改动位置 | 改动方式 |
|------|----------|----------|
| 新增资金字段 | `CAPITAL_FIELDS` + 新增 `fetch_xxx` 函数 + `sync_capital_flow` 字段分发分支 | 三处同步；新字段自动获得 bin 存储能力 |
| 修复沪市融资融券 | `fetch_margin` 的 SH 分支 | 循环日期批量拉取 `stock_margin_detail_sse`，映射到 `margin_balance` |
| 龙虎榜/融资融券批量优化 | `sync_capital_flow` 主入口 | 改为一次性拉取全市场接口 + 内存过滤，替代逐 code 拉取 |
| 北交所大单覆盖 | `fetch_big_order` 的 `market` 参数 | 扩展 market 取值以覆盖北交所 |
| 调整缺失日填充值 | 各 `fetch_xxx` 的填充逻辑 | 当前填 `0.0`，若需区分"未上榜"与"净买入为0"，可改填 NaN 并在因子层 `Fill` |

---

## 5. 其它采集器

### 5.1 市值数据（`market_data.py`）

- 产物：`total_mv`（总市值）写入 QLib bin
- 用途：市值中性化（`neutralize.py` 的 `market_cap_neutralize`）、组合优化约束

### 5.2 申万行业（`industry_sync.py`）

```python
def sync_industry_data() -> dict:
    """通过 akshare 获取申万一级行业，保存到 data/industry_map.json。"""

def load_industry_map() -> dict:
    """加载行业映射，供因子行业中性化与组合优化行业暴露约束使用。"""
```

- 产物：`data/industry_map.json`（{code: industry}）
- 用途：`industry_neutralize`、`portfolio_optimizer` 的 `max_industry_exposure` 约束
- 未同步时组合优化行业暴露约束不生效（仅告警）

### 5.3 指数同步（`index_sync.py`）

```python
def sync_indices_to_qlib(provider_uri: str, days: int = 365) -> dict:
    """拉取主要指数日K写入 qlib bin，日历中不存在的新日期自动扩展。"""
```

- 复用 `eod_incremental` 的 `_read_bin/_write_bin/_get_calendar` 等基础设施
- 支持指数：沪深300、上证50、中证500、中证1000、深证成指、创业板指、科创50、上证指数

### 5.4 EOD 增量同步（`eod_incremental.incremental_sync_eod`）

**函数签名**：
```python
async def incremental_sync_eod(
    universe: str = "csi300",     # 股票池
    days: int = 5,                # 同步最近 N 天（1-30）
    provider_uri: str = None,     # qlib 数据目录，默认 settings
    overwrite: bool = False,      # 是否覆盖已有日期（默认 False 仅追加新日期）
) -> dict:
```

**关键行为**：
- 基于 akshare 国内源，拉取最近 N 天日K转 qlib bin
- **默认仅追加新日期**（`overwrite=False`），避免 akshare qfq 与 chenditc 复权方式不同导致已有价格序列被覆盖
- `overwrite=True` 用于修复缺失数据
- 与 chenditc 全量同步互补：akshare 国内源访问快，适合日常增量

### 5.5 完整性校验（`integrity_check.check_integrity`）

```python
def check_integrity(provider_uri: str, universe: str = None) -> dict:
    """检测每只股票的 bin 文件长度是否与日历天数一致，返回缺失/异常清单。"""
```

通过 `GET /api/v1/quant/data/integrity-check?universe=csi300` 触发。

### 5.6 bin 读写基础设施（`eod_incremental.py`）

| 函数 | 签名 | 作用 |
|------|------|------|
| `_read_bin` | `(file_path: str)` | 读 QLib bin（float32，含起始日偏移头） |
| `_write_bin` | `(file_path: str, values: np.ndarray, start_index: int)` | 写 bin，start_index 为日历对齐偏移 |
| `_get_calendar` | `(provider_uri: str)` | 读 `calendars/day.txt` 交易日历 |
| `_write_calendar` | `(provider_uri: str, dates: list)` | 写/扩展日历 |
| `_read_instruments` | `(provider_uri: str, universe: str)` | 读 `instruments/{universe}.txt` 成分股 |
| `_qlib_code_to_akshare` | `(qlib_code: str)` | qlib 代码转 akshare 代码 |
| `_merge_calendar` / `_build_index_mapping` | — | 日历合并与索引映射，支持增量追加 |

---

## 6. 数据源说明

| 数据源 | 用途 | 接入方式 | 状态 |
|--------|------|---------|------|
| **chenditc（推荐主源）** | 预构建 qlib_bin 全量/增量包 | `chenditc_client.download_qlib_bin` / `incremental_sync.download_and_merge_incremental` | ✅ 已接入 |
| **akshare（兜底/增量）** | OHLCV / 基本面 / 资金 / 龙虎榜 / 指数 | 已封装，免费无 key | ✅ 已接入 |
| **mootdx（补）** | K线 / 财务快照 | 作为 akshare 接口 break 时的 fallback | 🟡 TODO |
| **tushare 免费额度** | 兜底 | 注册即送积分 | 🟡 TODO |

**chenditc 下载流程**（`chenditc_client.download_qlib_bin`）：
1. 下载 tar.gz 到临时文件
2. 解压到暂存目录（`strip components=1`）
3. 校验后再原子替换目标目录，避免中途失败污染现有数据

**akshare 已知风险**：
- 接口易 break（上游改版），需做异常捕获 + fallback
- 无 PIT 保证：历史数据可能被上游 revise，导致回测不可复现
- 高频访问被限流，`config.quant.fetch_interval_seconds=1.2` 控制请求间隔，`fetch_max_workers=3` 控制并发

**fallback 策略（待实现）**：
```
akshare 调用 → 失败/空 → mootdx → 失败 → tushare 免费额度 → 失败 → 记录缺口并告警
```

---

## 7. Alembic 迁移

### 7.1 目录与配置

| 项 | 值 |
|----|----|
| 迁移目录 | `backend/migrations/` |
| `alembic.ini` | `script_location = %(here)s/migrations` |
| 自动建表 | `init_db` 调 `Base.metadata.create_all` |
| 自动升级 | `init_db` 末尾在子线程跑 `alembic upgrade head`（失败仅告警不阻断） |

### 7.2 已有迁移版本

| revision | 说明 |
|----------|------|
| `23fc4c667c2f` | **基线迁移**：空 `upgrade`，表已由 `create_all` 创建 |
| `a1b2c3d4e5f6` | **增量迁移**：auto add columns |

### 7.3 新增模型字段后的操作流程

1. 修改 `FundamentalPIT`（或其他模型）加 `Column`
2. 生成增量迁移：
   ```bash
   cd backend && python -m alembic revision --autogenerate -m "描述本次字段变更"
   ```
3. 检查生成的迁移脚本（autogenerate 偶尔会误判，需人工核对）
4. `init_db` 会自动跑 `alembic upgrade head`；手动升级：
   ```bash
   cd backend && python -m alembic upgrade head
   ```

**注意事项**：
- 不要手改已 apply 的历史迁移脚本，只能新增 revision
- autogenerate 对类型变更/重命名识别不全，必要时手写 `op.alter_column` / `op.add_column`

### 7.4 SQLite 连接 PRAGMA（`database.py`）

每个新连接自动执行：
- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`（写不阻塞读）
- `PRAGMA busy_timeout = 5000`（写冲突等 5s 而非立即报 locked）
- `PRAGMA synchronous = NORMAL`（WAL 下兼顾安全与性能）
- `PRAGMA cache_size = -65536`（64MB 缓存）

---

## 8. 部署注意事项

### 8.1 时区

| 项 | 值 | 来源 |
|----|----|------|
| 应用时区 | `Asia/Shanghai` | `config.app.timezone` |
| 调度器时区 | `Asia/Shanghai` | `scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")` |
| 系统时区 | 本地系统时区 | 建议设为 `Asia/Shanghai`（本地部署） |
| 定时同步 | 工作日 18:00 | `register_scheduled_jobs`（mon-fri 18:00） |

> ⚠️ 本地部署时请确保系统时区为 `Asia/Shanghai`，否则 APScheduler 可能按 UTC 触发，同步时间会偏移 8 小时。

### 8.2 关键路径

| 用途 | 路径 | 配置项 |
|------|------|--------|
| QLib bin 数据 | `data/qlib_bin/cn_data/` | `config.quant.qlib_provider_uri` |
| SQLite 数据库 | `data/quantlab.db` | `config.data.db_path` |
| 原始数据 | `data/raw/` | `config.data.raw_dir` |
| 加工数据 | `data/processed/` | `config.data.processed_dir` |
| 模型产物 | `models/`（AutoML pkl 在 `data/models/automl/`） | `config.data.models_dir` |
| 日志 | `logs/` | `config.logging.dir` |
| 行业映射 | `data/industry_map.json` | `industry_sync.sync_industry_data` |

> 路径解析：`Settings.PROJECT_ROOT` 由环境变量 `PROJECT_ROOT` 决定，未设置时回退到代码所在目录上四级。所有相对路径基于 `PROJECT_ROOT` 拼接。

### 8.3 权限要求

- `data/`、`models/`、`logs/` 目录必须对运行用户**可写**（同步 bin、训练 AutoML、写日志）
- `config.yaml` 本地部署默认可读写；注意 `PUT /quant/data/data-source` 接口已 deprecated，前端已移除数据源下拉框，避免调用该接口
- QLib bin 目录替换（chenditc 全量同步）需原子 rename 权限，建议与暂存目录同文件系统

### 8.4 WSL 路径注意

- 项目位于 WSL `~/QuantLab`，**不要在 Windows 侧通过 `\\wsl$\` 直接编辑源码运行**，避免换行符/权限问题
- `.venv` 必须在 WSL 内创建：`python -m venv .venv`，使用 `.venv/bin/python`
- `start.sh` 默认用 `$SCRIPT_DIR/.venv/bin/python`，不存在时回退 `python3`
- qlib 依赖 `protobuf<4`、`setuptools<81`，安装时需约束版本（见 `requirements.txt`）

---

## 9. 已知问题完整清单

基于代码注释与 TODO 标记：

| # | 模块 | 问题 | 影响 | 位置 |
|---|------|------|------|------|
| 1 | eod_incremental | ST 股 5% 涨跌停未处理 | ST 股涨跌停日误判为可交易 | `_get_limit_pct` |
| 2 | eod_incremental | 新浪源无 pct_change 时用 close.pct_change 近似，除权日误判 | 极少样本误判 | `_compute_tradable` |
| 3 | eod_incremental | 历史 `$tradable` 仅增量同步时生成，无全量回填脚本 | 历史段缺 tradable | `incremental_sync_eod` |
| 4 | fundamental_sync | 财务报表（revenue/net_profit 等）解析 TODO | 财报字段无数据 | `fetch_financial_abstract` |
| 5 | capital_flow_sync | 沪市融资融券明细 `stock_margin_detail_sse` 仅单日查询，未实现 | `$margin_balance` 沪市为空 | `fetch_margin` |
| 6 | capital_flow_sync | 龙虎榜字段名"龙虎榜净买额"需运行时验证，akshare 版本可能不同 | 龙虎榜数据可能失败 | `fetch_dragon` |
| 7 | capital_flow_sync | 大单字段名"大单净流入-净额"需运行时验证 | 大单数据可能失败 | `fetch_big_order` |
| 8 | capital_flow_sync | 北交所 BJ 市场代码未验证，按 sh/sz 二分 | 北交所大单未覆盖 | `fetch_big_order` |
| 9 | data 源 | mootdx / tushare fallback 未实现 | akshare break 时无兜底 | §6 |
| 10 | data 源 | akshare 无 PIT 保证，历史可能被 revise | 回测不可复现 | §6 |
| 11 | 配置 | `config.yaml` data_source=akshare 与代码默认 chenditc 不一致 | 行为依赖配置，易混淆 | §1.4 |
| 12 | 因子引擎 | 因子协同性评估（相关性矩阵 + 增量 IC）未实现 | 仅有 IC 对比/衰减对比 | `factor_compare` |

---

## 10. 后续 TODO 清单

- [ ] 财务报表解析（`fetch_financial_abstract` 的 pivot 实现）
- [ ] 沪市融资融券采集（`fetch_margin` 的 SH 分支）
- [ ] 北交所大单覆盖（`fetch_big_order` 的 market 参数）
- [ ] ST 股 5% 涨跌停处理（需 ST 状态表）
- [ ] 历史 `$tradable` 字段回填（全量 `overwrite=True` 或专用脚本）
- [ ] mootdx 数据源接入（akshare break 时的 fallback）
- [ ] 因子协同性评估（相关性矩阵 + 增量 IC）
- [ ] LLM 挖掘闭环升级（假设 → 回测 → 反馈 → 沉淀）

---

## 附录 A：文件路径速查

| 模块 | 路径 |
|------|------|
| 日K增量+tradable | `backend/app/services/data/eod_incremental.py` |
| 基本面PIT | `backend/app/services/data/fundamental_sync.py` |
| 资金/情绪 | `backend/app/services/data/capital_flow_sync.py` |
| 市值数据 | `backend/app/services/data/market_data.py` |
| 申万行业 | `backend/app/services/data/industry_sync.py` |
| 指数同步 | `backend/app/services/data/index_sync.py` |
| chenditc 客户端 | `backend/app/services/data/chenditc_client.py` |
| 增量包合并 | `backend/app/services/data/incremental_sync.py` |
| 同步调度 | `backend/app/services/data/sync_runner.py` |
| 完整性校验 | `backend/app/services/data/integrity_check.py` |
| Alembic 迁移 | `backend/migrations/` |
| QLib bin 数据 | `{provider_uri}/features/{code_lower}/{field}.day.bin` |
| SQLite 库 | `backend/{db_name}.db`（由 `DATABASE_URL` 决定，实际 `data/quantlab.db`） |

## 附录 B：关键函数索引

| 函数 | 所在文件 | 作用 |
|------|----------|------|
| `_get_limit_pct` | `eod_incremental.py` | 按代码前缀返回涨跌停比例 |
| `_compute_tradable` | `eod_incremental.py` | 计算单股 tradable 序列 |
| `incremental_sync_eod` | `eod_incremental.py` | 日K增量同步 + tradable 生成 |
| `_read_bin` / `_write_bin` | `eod_incremental.py` | QLib bin 读写基础设施 |
| `_get_calendar` / `_write_calendar` | `eod_incremental.py` | 交易日历对齐 |
| `sync_fundamental_pit` | `fundamental_sync.py` | 基本面PIT采集 |
| `query_fundamental_pit` | `fundamental_sync.py` | PIT查询（按 announce_date） |
| `sync_capital_flow` | `capital_flow_sync.py` | 资金/情绪采集主入口 |
| `sync_industry_data` / `load_industry_map` | `industry_sync.py` | 行业同步与加载 |
| `sync_indices_to_qlib` | `index_sync.py` | 指数同步 |
| `download_qlib_bin` | `chenditc_client.py` | chenditc 全量包下载 |
| `download_and_merge_incremental` | `incremental_sync.py` | chenditc 增量包合并 |
| `run_sync_task` | `sync_runner.py` | 同步任务总调度 |
| `check_integrity` | `integrity_check.py` | bin 完整性校验 |

---

*文档版本：阶段1 · 最后更新：2026-07-31 · 基于代码审校增强*

---

## 十、baostock 数据源接入（2026-07-31）

### 架构变更：三层回退

```
1. chenditc (全量历史, 工作日18:00定时, 失败→baostock)
2. baostock (每日增量主源 + 个股首选, 失败→akshare)
3. akshare (仅个股/指数fallback)
```

### baostock 优势
- `query_daily_history_k_AStock(date)` 一次返回全市场某日K线（akshare需逐只爬）
- 自带 `isST` 字段（解决ST股5%涨跌停mask bug）
- 自带 `peTTM/pbMRQ/psTTM/pcfNcfTTM` 估值字段（重建fundamental_pit表）
- 官方稳定，不限频

### 新增模块
| 模块 | 文件 | 说明 |
|---|---|---|
| baostock客户端 | `services/data/baostock_client.py` | login单例+线程池+全市场/单股K线 |
| EOD增量(baostock) | `services/data/eod_incremental.py` | `incremental_sync_eod(source='baostock')` |
| 基本面PIT | `services/data/fundamental_sync.py` | `sync_fundamental_pit` + `query_fundamental_pit` |
| 基本面模型 | `models/fundamental.py` | `FundamentalPIT` 表(code+trade_date PK) |
| 回退链 | `services/data/sync_runner.py` | chenditc→baostock回退 |
| 兜底API | `api/quant_data.py` | `POST /quant/data/fallback-sync` |

### ST股5%涨跌停mask修复
`_compute_tradable` 新增 `is_st` 参数：
- baostock 返回 `isST` 字段（'1'=ST, '0'=非ST）
- ST股涨跌停阈值从板块值（10%/20%/30%）降为5%
- akshare fallback路径无isST，保持原逻辑（向后兼容）

### fundamental_pit 表重建
- 旧表（死代码未接入）已drop
- 新表 schema：`code + trade_date` 复合PK + `pe_ttm/pb_mrq/ps_ttm/pcf_ncf_ttm`
- 迁移 revision：`b2c3d4e5f6g7`
- PIT查询：`WHERE trade_date <= 查询日 ORDER BY trade_date DESC LIMIT 1`

### 手动兜底同步API
```
POST /api/v1/quant/data/fallback-sync
  ?days=5          # 回溯天数(1-60)
  &source=baostock # baostock(推荐) / akshare
```
