# QuantLab 数据层技术文档（阶段1改造）

> 适用范围：QuantLab 阶段1数据层改造
> 文档目的：记录数据层架构、每个文件/函数/参数说明、后期手动修改指引
> 维护原则：每次数据层代码变更须同步更新本文档对应小节

---

## 1. 数据层架构概览

### 1.1 整体分层

```
数据源（akshare/mootdx/tushare）
    ↓
采集器层（backend/app/services/data/）
  - eod_incremental.py    日K OHLCV + $tradable mask
  - fundamental_sync.py   基本面PIT（估值日频 + 财务报表TODO）
  - capital_flow_sync.py  资金/情绪（北向/龙虎榜/融资融券/大单）
  - market_data.py        市值数据
  - industry_sync.py      申万行业
    ↓
存储层
  - QLib bin   量价/资金字段，日频，features/{code}/{field}.day.bin
  - SQLite     基本面PIT宽表，按 announce_date 查询
    ↓
因子引擎（QLib 表达式引用 $close/$tradable/$north_net 等）
```

### 1.2 存储双轨制设计

| 存储介质 | 承载内容 | 访问方式 | 写入入口 |
|----------|----------|----------|----------|
| QLib bin（float32） | 量价 OHLCV、$tradable mask、资金/情绪日频字段 | QLib 表达式 `$field` | `_write_bin` |
| SQLite（关系表） | 基本面 PIT 宽表（含 announce_date） | `query_fundamental_pit` | SQLAlchemy `session.add` |

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
| `industry_sync.py` | 申万行业分类 | industry bin/表 | ✅ 已实现 |

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
def _get_limit_pct(code: str) -> float:
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
- 入参 `code` 是 qlib 风格代码（如 `SH600000`、`SZ300750`、`BJ830799`）
- 取字母后的数字部分前缀匹配，前缀命中即返回对应比例
- 未命中任何前缀时返回默认 `0.10`（保守按主板处理）

### 2.4 计算逻辑（`_compute_tradable` 函数）

**函数签名**：
```python
def _compute_tradable(
    df: pd.DataFrame,      # 含 close / pct_change（可选）的OHLCV DataFrame
    code: str,             # qlib代码，用于查涨跌停比例
) -> pd.Series:
    """返回 float32 序列：1.0 可交易，0.0 触及涨跌停。"""
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

## 5. 数据源说明

| 数据源 | 用途 | 接入方式 | 状态 |
|--------|------|---------|------|
| **akshare（主）** | OHLCV / 基本面 / 资金 / 龙虎榜 | 已封装，免费无 key | ✅ 已接入 |
| **mootdx（补）** | K线 / 财务快照 | 作为 akshare 接口 break 时的 fallback | 🟡 TODO |
| **tushare 免费额度** | 兜底 | 注册即送积分 | 🟡 TODO |

**akshare 已知风险**：
- 接口易 break（上游改版），需做异常捕获 + fallback
- 无 PIT 保证：历史数据可能被上游 revise，导致回测不可复现
- 高频访问被限流，建议加请求间隔与重试退避

**fallback 策略（待实现）**：
```
akshare 调用 → 失败/空 → mootdx → 失败 → tushare 免费额度 → 失败 → 记录缺口并告警
```

---

## 6. Alembic 迁移

### 6.1 目录与配置

| 项 | 值 |
|----|----|
| 迁移目录 | `backend/migrations/` |
| `alembic.ini` | `script_location = %(here)s/migrations` |
| 自动建表 | `init_db` 调 `create_all` |
| 自动升级 | `init_db` 末尾跑 `alembic upgrade head` |

### 6.2 已有迁移版本

| revision | 说明 |
|----------|------|
| `23fc4c667c2f` | **基线迁移**：空 `upgrade`，表已由 `create_all` 创建 |
| `a1b2c3d4e5f6` | **增量迁移**：auto add columns |

### 6.3 新增模型字段后的操作流程

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

---

## 7. 后续 TODO 清单

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
| Alembic 迁移 | `backend/migrations/` |
| QLib bin 数据 | `{provider_uri}/features/{code_lower}/{field}.day.bin` |
| SQLite 库 | `backend/{db_name}.db`（由 `DATABASE_URL` 决定） |

## 附录 B：关键函数索引

| 函数 | 所在文件 | 作用 |
|------|----------|------|
| `_get_limit_pct` | `eod_incremental.py` | 按代码前缀返回涨跌停比例 |
| `_compute_tradable` | `eod_incremental.py` | 计算单股 tradable 序列 |
| `incremental_sync_eod` | `eod_incremental.py` | 日K增量同步 + tradable 生成 |
| `_read_bin` / `_write_bin` | `eod_incremental.py` | QLib bin 读写基础设施 |
| `_get_calendar` | `eod_incremental.py` | 交易日历对齐 |
| `sync_fundamental_pit` | `fundamental_sync.py` | 基本面PIT采集 |
| `query_fundamental_pit` | `fundamental_sync.py` | PIT查询（按 announce_date） |
| `fetch_valuation_daily` | `fundamental_sync.py` | 拉估值日频 |
| `fetch_financial_abstract` | `fundamental_sync.py` | 拉财务报表（TODO） |
| `sync_capital_flow` | `capital_flow_sync.py` | 资金/情绪采集主入口 |
| `fetch_north` / `fetch_margin` / `fetch_dragon` / `fetch_big_order` | `capital_flow_sync.py` | 四类资金字段拉取 |

---

*文档版本：阶段1 · 最后更新：2026-07-31*
