---
title: 因子引擎
slug: factor-engine
order: 4
group: 因子
summary: QLib表达式语法、算子白名单、因子分类、评价指标、加权、挖掘与回测
---

# 因子引擎

> 本文档记录 QuantLab 因子引擎的完整链路：表达式语法与安全沙箱、因子分类、评价指标、
> 因子加权、挖掘方式（LLM/符号回归/AutoML/文本）与回测后端。
> 所有算子、字段、函数签名均来自 `services/factor/` 与 `services/quant/` 实际代码。

---

## 1. 因子表达式语法

### 1.1 字段引用

因子表达式以 QLib 语法书写，字段以 `$` 开头，引用 QLib bin 中的日频列。**允许的字段白名单**（`factor/expression.py` `_QLIB_FIELDS`）：

| 字段 | 含义 | 字段 | 含义 |
|------|------|------|------|
| `$open` | 开盘价 | `$volume` | 成交量 |
| `$close` | 收盘价 | `$amount` | 成交额 |
| `$high` | 最高价 | `$factor` | 复权因子 |
| `$low` | 最低价 | `$change` | 涨跌量 |

数据层扩展字段（资金/情绪/市值，详见 DATA_LAYER.md）：`$tradable`、`$north_net`、`$margin_balance`、`$dragon_net`、`$big_order_net`、`$total_mv` 等。

### 1.2 算子白名单

表达式经安全沙箱 `validate_expression` 校验，**允许的算子 = `config.mining.llm.allowed_ops` ∪ `_QLIB_OPS`**。

`config.yaml` 配置的白名单（推荐算子）：

| 算子 | 语义 | 示例 |
|------|------|------|
| `Ref(x, n)` | 取 n 期前的值（**n 为正=过去**，负数=未来，被禁） | `Ref($close, 20)` |
| `Mean(x, n)` | n 日均值 | `Mean($close, 20)` |
| `Std(x, n)` | n 日标准差 | `Std($close, 20)` |
| `Max(x, n)` / `Min(x, n)` | n 日最大/最小 | `Max($close, 20)` |
| `Sum(x, n)` | n 日累加 | `Sum($volume, 5)` |
| `Rank(x)` | 截面排名 | `Rank($close)` |
| `Corr(x, y, n)` | n 日 Pearson 相关 | `Corr($close, Log($volume+1), 10)` |
| `Cov(x, y, n)` | n 日协方差 | `Cov($close, $volume, 20)` |
| `Delta(x, n)` | `x - Ref(x, n)` | `Delta($close, 5)` |
| `Slope(x, y, n)` | n 日回归斜率 | `Slope($close, $volume, 20)` |
| `Resi(x, y, n)` | n 日回归残差 | `Resi($close, $volume, 20)` |
| `WMA(x, n)` | 加权移动平均 | `WMA($close, 20)` |
| `EMA(x, n)` | 指数移动平均 | `EMA($close, 12)` |

沙箱内置全集 `_QLIB_OPS`（与配置白名单取并集）还含：`Var`、`Quantile`、`MA`、`RSRS`、`Greater/Less/Gt/Lt/Ge/Le/Eq/Ne`、`Abs`、`Log`、`Power`、`Sign`、`If`、`IdxMax/IdxMin`、`Product`、`Count`、`Mad`、`Clip`、`Range`、`Floor/Ceil`、`All/Any`、`Pair`、`Bias`、`Div/Sub/Add/Mul`。

### 1.3 复合表达式写法

```python
# 20 日动量
$close / Ref($close, 20) - 1

# 量价相关性
Corr($close, Log($volume + 1), 10)

# 波动率
Std($close / Ref($close, 1) - 1, 20)

# 振幅
Mean(($high - $low) / $close, 20)

# 条件信号
If($dragon_net > 0, 1, 0)
```

### 1.4 安全沙箱（`validate_expression`）

**函数签名**：
```python
def validate_expression(expr: str, max_length: int = 2000) -> str:
    """校验因子表达式安全性，返回清洗后的表达式。"""
```

校验规则：
1. 非空且长度 ≤ 2000
2. 禁止危险关键字：`__`、`compile`、`builtins`、`automl`、`autogluon`、`import`、`exec`、`eval`、`lambda`、`os`、`sys`、`subprocess`、`globals`、`locals`、`getattr`、`setattr`
3. 禁止 `open()` 调用（与 `$open` 字段区分检测）
4. `$field` 必须在 `_QLIB_FIELDS` 内
5. 标识符必须在白名单内（配置 `allowed_ops` ∪ `_QLIB_OPS`）
6. `$field` 替换为 `x_field` 后 AST 解析，禁止 `import`、下划线属性访问
7. **look-ahead 防护**：AST 检测 `Ref(..., 负常量)`，命中即拒（`_find_negative_ref`）

**前向收益标签例外**：标签表达式 `Ref($close, -1) / $close - 1` 用未来收益作预测目标是正确的，仅在 `load_label` 中使用，不经过因子校验。

### 1.5 防前视检查（`check_lookahead`）

```python
def check_lookahead(expr: str) -> None:
    """仅检查负数 Ref（look-ahead bias），不做白名单校验。"""
```

- 在 `load_factor_values` 执行入口做防御性检查
- 即便表达式绕过创建时的完整校验（`skip_validation=True`），也保证不会加载未来数据
- 非标准表达式（AutoML/TextSentiment 占位符）语法解析失败时直接放行，交由上游处理

---

## 2. 因子分类

`Factor.category` 字段取值（代码实际使用，模型注释仅写 4 种，实际 6 种）：

| category | 来源 | 表达式形态 | 入库方式 |
|----------|------|-----------|----------|
| `builtin` | 内置因子种子 | 标准 qlib 表达式 | `seed_builtin_factors`（`POST /factors/seed-builtin`） |
| `alpha158` | QLib Alpha158 基准集 | 标准 qlib 表达式 | `seed_alpha158`（`POST /factors/seed-alpha158`） |
| `llm` | LLM 生成 | 标准 qlib 表达式（沙箱校验） | `mine_with_llm` 自动入库 |
| `symbolic` | 符号回归/遗传规划 | 翻译自 gplearn 程序 | `mine_with_symbolic` 自动入库 |
| `text` | 文本情绪因子 | `TextSentiment(...)` 占位符（不支持实时计算） | `mine_with_text` 自动入库 |
| `automl` | AutoML 组合因子 | `AutoML(method, task_id)` 占位符 | `mine_with_automl` 自动入库 |

> ⚠️ **代码/注释不一致**：`models/factor.py` 注释写 `builtin / llm / symbolic / text`，实际代码还使用 `alpha158` 与 `automl` 两类。

**特殊表达式**：
- `AutoML(lightgbm, task_id)` / `AutoML(linear, task_id)`：`load_factor_values` 拦截后加载 `data/models/automl/{task_id}.pkl` bundle，重建基础特征并模型预测
- `TextSentiment(...)`：qlib 未注册算子，`load_factor_values` 直接抛 `ValueError`，需重新挖掘预计算值

**因子状态**：`active` / `disabled`（删除即 `disable_factor` 置 disabled）/ `verified`（`auto-import` 达标标记）。

---

## 3. 因子评价

评价核心在 `services/quant/factor_eval.py`，基于 qlib `D.features` 加载数据 + pandas 手动计算（不依赖 qlib.contrib.eval 不稳定 API）。

### 3.1 数据加载

```python
def load_factor_values(
    factor_expr: str, start: str, end: str,
    universe: str = None, neutralize: str = None,
) -> pd.DataFrame:
    """加载因子值，返回 MultiIndex (datetime, instrument) DataFrame，列名 factor。
    neutralize: None / "market_cap" / "industry"（行业+市值）"""
```

- 股票池：`_load_instruments(market)`，默认过滤北交所（`include_bj=False`）
- AutoML 表达式拦截 → `_load_automl_factor` 加载 bundle 预测
- 执行前 `check_lookahead` 防前视

```python
def load_label(start: str, end: str, label_expr: str = None, universe: str = None) -> pd.DataFrame:
    """加载前向收益标签，默认 _DEFAULT_LABEL = "Ref($close, -1) / $close - 1"
    （t 日收盘到 t+1 日收盘收益，与回测引擎 shift(-1) 口径一致）"""
```

### 3.2 IC（信息系数）

```python
def compute_ic(factor_df: pd.DataFrame, label_df: pd.DataFrame) -> dict:
    """计算 IC / RankIC / ICIR / IR。"""
```

| 指标 | 计算方式 |
|------|---------|
| **IC** | 每日截面 Pearson 相关 `factor.corr(label)` 的均值 |
| **RankIC** | 每日截面 Spearman 相关的均值 |
| **ICIR** | `IC 均值 / IC 标准差` |
| **IR** | `RankIC 均值 / RankIC 标准差` |
| `n_days` | 有效截面天数 |

> 每日截面要求 `len(g) >= 2`，否则记 NaN 并 dropna。结果 `round(., 4)`。

### 3.3 换手率

```python
def compute_turnover(factor_df: pd.DataFrame) -> float:
    """每日取 topk（config.quant.topk，默认 50），计算与前一日持仓重合度，
    turnover = 1 - overlap / len(prev)。返回日均换手。"""
```

### 3.4 衰减分析（decay）

```python
def compute_decay(factor_df: pd.DataFrame, label_df: pd.DataFrame, max_lag: int = 10) -> dict:
    """因子与未来 1~max_lag 日收益的 IC 序列。
    一次查询 $close 后本地 shift 计算各 lag 前向收益，避免 N 次 qlib IO。"""
```

返回 `{lag: ic}`。`get_factor_decay` 进一步计算：
- **半衰期** `half_life`：IC 衰减到首日一半所需期数
- **有效期** `effective_period`：IC 绝对值 ≥ 0.02 的最后期数

### 3.5 分层回测（`compute_quantile_returns`）

```python
def compute_quantile_returns(
    factor_df, return_df, n_groups: int = 5,
    factor_col="factor", return_col="label",
) -> dict:
```

- 每个截面按因子值 `pd.qcut` 分 `n_groups` 组（重复值失败时降级用 `rank`）
- 输出：`group_returns`、`group_nav`（累计净值）、`group_stats`（各组年化/夏普/天数）、`long_short_returns`（最高组-最低组）、`long_short_nav`、`monotonicity_score`（组号与组均收益的 Spearman 相关）

### 3.6 因子深度分析（`deep_analyze_factor`）

一次性聚合所有分析，走进程池 `run_cpu`（`GET /factors/{id}/deep-analysis`）：

```python
def deep_analyze_factor(
    factor_expr, start, end, universe=None,
    horizon: int = 5, n_groups: int = 5, ic_window: int = 60,
) -> dict:
```

返回结构：
- `config`：参数
- `summary`：`ic_mean`/`ic_std`/`icir`/`t_stat`/`p_value`/`significant`/`avg_turnover`/`annual_turnover`/`long_short_annual_return`/`monotonicity`
- `ic_distribution`：分箱统计（mean/std/skew/positive_ratio，需 scipy）
- `ic_timeseries`：每日 IC + 滚动均线（窗口 `ic_window`）
- `quantile_returns`：`compute_quantile_nav_by_horizon`（按 horizon 调仓的分层累计净值）
- `turnover_curve`：多头组换手率时序
- `decay`：`{lags, ic_by_lag}`

**IC 显著性**（`compute_ic_significance`）：双尾 t 检验，`p_value < 0.05` 为显著；注意未经 Newey-West 自相关调整。

> label 用 `Ref($close, -horizon)/$close - 1`（horizon 周期前向收益），区别于默认 1 日标签。

### 3.7 因子协同性评估（现状）

> ⚠️ **诚实声明**：任务要求的"相关性矩阵 + 增量 IC"**当前未实现**。现有协同性能力为：
> - **因子对比**（`compare_factors`）：返回多因子 IC 指标对比、衰减对比（`decay_comparison`）、IC 时序对比（`ic_timeseries`）
> - **正交化**（`orthogonalize.gram_schmidt_orthogonalize`）：按 IC 绝对值降序做 Gram-Schmidt 截面正交化，降低共线性（策略可启用 `orthogonalize=1`）
> - **中性化**（`neutralize`）：`market_cap` / `industry`（行业+市值），对比中性化前后 IC
>
> 完整相关性矩阵与增量 IC 已列入 TODO（见 DATA_LAYER.md §10）。

---

## 4. 因子加权

`backtest_engine.combine_factors` 将多因子组合为打分：

```python
def combine_factors(
    factor_values: dict, weights: dict = None,
    method: str = "equal_weight", orthogonalize: bool = False,
) -> pd.DataFrame:
    """返回 MultiIndex DataFrame，含 'score' 列。"""
```

| method | 权重计算 |
|--------|---------|
| `equal_weight` | 等权 `1/n`（忽略 weights） |
| `ic_weight` | `abs(weight) / sum(abs(weights))`，weight 取因子库 IC 字段 |
| `ir_weight` | 同上，weight 取因子库 ICIR 字段 |

**流程**：
1. 可选 Gram-Schmidt 正交化（按 IC 绝对值降序）
2. 各因子截面 z-score 标准化（`ddof=0` 防单元素组 std=NaN）
3. 按权重加权得 `score`

**滚动权重计算**（`compute_combine_weights`）：

```python
def compute_combine_weights(
    factor_exprs: dict, start, end,
    method: str = "ic_weight", window: int = 60, horizon: int = 5,
    universe: str = None,
) -> dict:
    """从历史滚动 IC/ICIR 自动计算组合权重。"""
```

- `ic_weight`：取最近 `window` 个交易日的 RankIC 均值
- `ir_weight`：取最近 `window` 日的 `RankIC 均值 / RankIC 标准差`
- 标签用 `Ref($close, -horizon)/$close - 1`
- 按绝对值归一化（和为 1，保留符号）；全 0 时退化为等权

> 策略回测时（`run_strategy_backtest`）：`ir_weight` 用因子库 `icir` 字段，`ic_weight` 用 `ic` 字段作为静态权重传入。

---

## 5. 因子挖掘

四种挖掘方式，统一经 `_safe_run_task` 包装（信号量限流 `task.max_concurrent`、超时分级、异常兜底标 failed）。

### 5.1 LLM 挖掘（`mine_with_llm`）

```python
async def mine_with_llm(task_id: int, n_candidates: int = None) -> dict:
async def mine_with_llm_iterative(task_id: int, n_rounds: int = 3, n_candidates: int = None) -> dict:
```

**流程**：
1. 用 `_USER_PROMPT_TEMPLATE` 构造提示（含算子/字段/语法示例/look-ahead 警告）
2. `ProviderRouter.route_request` 调 LLM（三级故障转移），强制返回 JSON
3. 逐候选：`validate_expression` 沙箱校验 → `_evaluate_safe` IC 评价（线程池，`eval_timeout_seconds` 超时）→ IC 绝对值 ≥ `ic_threshold`(0.03) 入库（category=llm）
4. 更新任务统计：`candidates_generated`/`candidates_passed`/`best_ic`/`result_factor_ids`

**迭代挖掘**（`n_rounds > 1`）：每轮生成→校验→IC评价→`_build_feedback_prompt` 反馈给 LLM 逐轮改进。

**超时策略**：不限时，依赖内部原子超时（provider httpx timeout + `eval_timeout_seconds`）+ `llm_hard_limit_seconds`（默认 7200s，0=完全无限）硬上限兜底。

### 5.2 符号回归（`mine_with_symbolic`）

```python
async def mine_with_symbolic(task_id: int) -> dict:
```

**流程**（gplearn 遗传规划）：
1. 12 个基础特征作为终端（`_BASE_FEATURES`：mom_5/mom_20/vol_20/vol_60/turn_5/turn_20/vratio/amp_20/ma_div_20/ma_div_60/high_dd_20/rsi_20）
2. 扩展函数集（add/sub/mul/div/log/abs/sign/max/min/if）
3. `SymbolicRegressor` 演化（population=1000, generations=30, tournament_size=20, parsimony_coefficient=0.001）
4. `_translate_program` 将最优程序翻译为 qlib 表达式（add→Add, Xi→子表达式）
5. 沙箱校验 + IC 评价 + 入库（category=symbolic）

### 5.3 AutoML（`mine_with_automl`）

```python
async def mine_with_automl(task_id: int, factor_ids: list[int], method: str = None) -> dict:
```

**流程**：
1. 加载指定基础因子值（AutoML bundle 丢失/文本算子不可用时跳过该因子）
2. 截面标准化后训练 `lightgbm`/`linear` 模型预测前向收益
3. `time_series_cv_eval` 时序交叉验证（n_splits=5）
4. `joblib` 持久化到 `data/models/automl/{task_id}.pkl`（含 `feature_names`/`factor_expressions` bundle）
5. SHAP 特征重要性写入任务结果
6. 入库 `AutoML(method, task_id)` 占位符因子（category=automl）

**回测支持**：`load_factor_values` 遇 `AutoML(...)` 正则匹配后加载 bundle 重建特征预测。

### 5.4 文本因子（`mine_with_text`）

```python
async def mine_with_text(task_id: int, codes: list[str] = None) -> dict:
```

**流程**：
1. `_fetch_news_for_universe` 拉取新闻（默认 universe 前 30 只，`max_news_per_day=50`）
2. `_classify_sentiment` LLM 批量情绪分类（batch_size=20，返回 score 1/0/-1）
3. 聚合为每日截面情绪因子
4. IC 评价 + 入库（category=text，表达式为 `TextSentiment(...)` 占位符）

> ⚠️ 文本因子表达式为占位符，`load_factor_values` 不支持实时计算，不可直接用于 qlib 回测/深度分析。

### 5.5 防前视检查

所有挖掘路径在入库前经 `validate_expression`（含 AST 负数 Ref 检测），执行入口再经 `check_lookahead` 双重防护，杜绝 look-ahead bias。

---

## 6. 回测

### 6.1 回测后端

`backtest_engine.run_backtest` 支持 `backend` 切换：

```python
def run_backtest(
    score_df, start=None, end=None, topk=None, n_drop=None,
    benchmark=None, rebalance_freq="day", portfolio_method=None,
    backend: str = "qlib",
) -> dict:
```

| backend | 实现 | 特点 |
|---------|------|------|
| `qlib`（默认） | `qlib_backtest.run_qlib_backtest`：QLib `backtest_daily` + `TopkDropoutStrategy` | 工业级，原生 A 股约束 |
| `self` | `run_backtest` 自研回测：逐日循环 | 含涨跌停/停牌/调仓频率/成本/组合优化 |

### 6.2 QLib 回测后端（`TopkDropoutStrategy`）

A 股交易约束由 QLib Exchange 原生处理：

| 约束 | 配置 |
|------|------|
| 涨跌停 | `limit_threshold=0.095`（涨停不可买、跌停不可卖） |
| T+1 | `deal_price="close"` + signal 时序（T 日决策，T+1 收盘成交） |
| 停牌 | `only_tradable=True` 自动过滤 |
| 买入成本 | `open_cost=cost_buy`（默认 0.0013） |
| 卖出成本 | `close_cost=cost_sell`（默认 0.0023） |
| 最小成本 | `min_cost=5` |
| 滑点 | `impact_cost=slippage_bps/10000`（`slippage_bps>0` 时启用） |

策略参数：
- `topk`（默认 50）、`n_drop`（默认 5）、`method_sell="bottom"`、`method_buy="top"`
- `hold_thresh`：`{day:1, week:5, month:20}` 控制非调仓日持仓
- 初始资金 `account=100000000`

### 6.3 自研回测后端

- **涨跌停过滤**：`_is_price_limited`（主板 ±9.5%，创业板/科创板 ±19.5%），涨停不可买入、跌停不可卖出（dropout 时跌停强制保留）
- **停牌过滤**：成交量为 0 或收益 NaN 排除
- **调仓频率**：day（每日）/ week（每 5 交易日）/ month（月初）
- **交易成本**：单边换手率 × (cost_buy + cost_sell)
- **组合优化**：`portfolio_method=cvxpy_optimize` 时调 `portfolio_optimizer.optimize_portfolio`（受 `portfolio_optimizer.enabled` 控制）

### 6.4 评价指标（`portfolio.analyze_portfolio`）

纯 pandas/numpy 计算，A 股年化 `TRADING_DAYS=252`：

| 指标 | 计算方式 |
|------|---------|
| `annual_return` | `(1+r).prod() ** (1/years) - 1`，years = len/252 |
| `annual_volatility` | `r.std() * sqrt(252)` |
| `sharpe` | `r.mean() / r.std() * sqrt(252)` |
| `sortino` | `r.mean() / downside.std() * sqrt(252)` |
| `max_drawdown` | `((1+r).cumprod() / cummax - 1).min()` |
| `calmar` | `annual_return / abs(max_drawdown)` |
| `win_rate` | `(r > 0).sum() / len(r)` |
| `benchmark_return` | 基准年化 |
| `excess_return` | 组合年化 - 基准年化 |

净值曲线 `build_nav_curve`：组合与基准归一化到 1.0。

### 6.5 walk-forward 滚动回测

`POST /strategies/{id}/walk-forward`（`services/quant/walk_forward`）：
- 训练窗选最优 topk，测试窗做样本外验证
- 参数：`train_window`(730D)/`test_window`(180D)/`step`(180D)/`topk_list`/`n_drop`/`rebalance`
- 评估跨窗一致性，结果存 `TaskResult`（task_type=walk-forward）

### 6.6 参数扫描

`POST /strategies/{id}/param-sweep`（`strategy/param_sweep`）：
- 笛卡尔积 `topk_list × rebalance_list`，默认 `[10,20,30,50] × [day,week]`
- 结果存 `TaskResult`（task_type=param-sweep）

---

## 7. 因子库 CRUD 与评价调度

`services/factor/library.py`：

| 函数 | 说明 |
|------|------|
| `list_factors(category, status, sort_by, limit, offset)` | 列表，sort_by ∈ ic/rank_ic/icir/created_at |
| `get_factor(factor_id)` | 详情 |
| `add_factor(name, expression, category, description, source_task_id, skip_validation)` | 新增（默认带沙箱校验） |
| `disable_factor(factor_id)` | 软删除（status=disabled） |
| `update_factor_metrics(factor_id, metrics)` | 写回 IC/RankIC/ICIR/IR/turnover/decay |
| `evaluate_factor_by_id(factor_id, start, end)` | 评价库中因子，走 `run_cpu` 进程池 |
| `seed_builtin_factors` / `seed_alpha158` | 种子内置因子 |

**Factor 模型字段**：`id/name/expression/category/description/ic/rank_ic/icir/ir/turnover/decay(JSON)/eval_start/eval_end/evaluated_at/status/source_task_id/created_at`。

---

## 8. 相关 API 速查

| 功能 | 端点 |
|------|------|
| 因子列表 | `GET /api/v1/factors` |
| 因子详情 | `GET /api/v1/factors/{id}` |
| 新增因子 | `POST /api/v1/factors` |
| 因子评价 | `POST /api/v1/factors/{id}/evaluate` |
| 因子对比 | `POST /api/v1/factors/compare` |
| 衰减分析 | `GET /api/v1/factors/{id}/decay` |
| 分层回测 | `GET /api/v1/factors/{id}/quantile-analysis` |
| 中性化 | `POST /api/v1/factors/{id}/neutralize` |
| 深度分析 | `GET /api/v1/factors/{id}/deep-analysis` |
| 策略回测 | `POST /api/v1/strategies/{id}/backtest` |
| 参数扫描 | `POST /api/v1/strategies/{id}/param-sweep` |
| walk-forward | `POST /api/v1/strategies/{id}/walk-forward` |
| LLM 挖掘 | `POST /api/v1/mining/llm` |
| 符号回归 | `POST /api/v1/mining/symbolic` |
| AutoML | `POST /api/v1/mining/automl` |
| 文本因子 | `POST /api/v1/mining/text` |

详见 [API_REFERENCE.md](./API_REFERENCE.md)。

---

## 9. 已知问题与 TODO

- 因子协同性评估（相关性矩阵 + 增量 IC）未实现，仅有 IC 对比/衰减对比/正交化/中性化
- 文本因子（`TextSentiment`）与 AutoML（`AutoML(...)`）为占位符表达式，不可直接用于 qlib 回测/深度分析；策略回测会跳过此类因子
- `Factor.category` 模型注释与实际使用不一致（缺 alpha158/automl）
- `compare_factors` 文档字符串写 `ic_comparison`，实际返回字段为 `ic_timeseries`
- IC 显著性 t 检验未经 Newey-West 自相关调整

---

*文档版本：1.0 · 最后更新：2026-07-31*
