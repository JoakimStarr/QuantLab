---
title: 因子引擎
slug: factor-engine
order: 4
group: 因子
summary: QLib表达式语法、算子白名单、因子分类、评价指标、加权、挖掘与回测
---

# 因子引擎

> 文档版本：v2.5.5 · 最后更新：2026-08-01
> 本文档记录 QuantLab 因子引擎的完整链路：表达式语法与安全沙箱、因子分类、评价指标、
> 因子加权、挖掘方式（LLM/符号回归/AutoML/文本）与回测后端。
> 所有算子、字段、函数签名均来自 `services/factor/` 与 `services/quant/` 实际代码。

---

## 这是什么文档

本文档专门讲"**因子怎么写、怎么算、怎么挖、怎么用**"。如果你：

- 想自定义因子 → 看 §1（语法）+ §2（沙箱）
- 想理解评价指标 → 看 §3
- 想让 AI 帮你挖因子 → 看 §4（挖掘）
- 想用因子跑回测 → 看 §5（回测）
- 想知道 API 怎么调 → 看 §6

---

## 一、因子表达式语法

### 1.1 字段引用（什么数据可以用）

因子表达式以 QLib 语法书写，字段以 `$` 开头，引用 QLib bin 中的日频列。

**允许的字段白名单**（`factor/expression.py` `_QLIB_FIELDS`）：

| 字段 | 含义 | 字段 | 含义 |
|------|------|------|------|
| `$open` | 开盘价 | `$volume` | 成交量 |
| `$close` | 收盘价 | `$amount` | 成交额 |
| `$high` | 最高价 | `$factor` | 复权因子 |
| `$low` | 最低价 | `$change` | 涨跌量 |

**数据层扩展字段**（资金/情绪/市值，详见 [DATA_LAYER.md](DATA_LAYER.md)）：
- `$tradable` — 是否可交易（ST/停牌过滤）
- `$north_net` — 北向资金净流入
- `$margin_balance` — 融资余额
- `$dragon_net` — 龙虎榜净买入
- `$big_order_net` — 大单净买入
- `$total_mv` — 总市值

### 1.2 算子白名单（能做什么运算）

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

**沙箱内置全集 `_QLIB_OPS`**（与配置白名单取并集）还含：
- `Var`、`Quantile`、`MA`、`RSRS`
- `Greater/Less/Gt/Lt/Ge/Le/Eq/Ne`（比较）
- `Abs`、`Log`、`Power`、`Sign`
- `If`、`IdxMax/IdxMin`
- `Product`、`Count`、`Mad`、`Clip`、`Range`、`Floor/Ceil`
- `All/Any`、`Pair`、`Bias`
- `Div/Sub/Add/Mul`（四则运算）

### 1.3 复合表达式写法（实战示例）

```python
# 20 日动量：当前价 / 20日前价 - 1
$close / Ref($close, 20) - 1

# 量价相关性：10 日 close 与 log(volume+1) 的相关性
Corr($close, Log($volume + 1), 10)

# 波动率：20 日日收益率标准差
Std($close / Ref($close, 1) - 1, 20)

# 振幅：20 日平均日内振幅
Mean(($high - $low) / $close, 20)

# RSI 简化版：20 日上涨概率 - 下跌概率
Mean(Greater($close - Ref($close, 1), 0), 20) -
Mean(Greater(Ref($close, 1) - $close, 0), 20)

# 资金信号：北向资金净流入为正时取 1
If($north_net > 0, 1, 0)

# Alpha158 标准因子示例
($close - $open) / ($high - $low + 1e-12)        # KMID2
Ref($close, 5) / $close                           # ROC5
Std($close, 20) / $close                          # STD20
```

### 1.4 安全沙箱（`validate_expression`）

**函数签名**：
```python
def validate_expression(expr: str, max_length: int = 2000) -> str:
    """校验因子表达式安全性，返回清洗后的表达式。"""
```

**校验规则**（7 道关卡）：
1. **非空**且长度 ≤ 2000 字符
2. **禁止危险关键字**：`__`、`compile`、`builtins`、`automl`、`autogluon`、`import`、`exec`、`eval`、`lambda`、`os`、`sys`、`subprocess`、`globals`、`locals`、`getattr`、`setattr`
3. **禁止 `open()` 调用**（与 `$open` 字段区分检测，避免误伤）
4. **`$field` 必须在白名单内**（`$close/$open/$high/$low/$volume/$amount/$factor/$change`）
5. **标识符必须在白名单内**（配置 `allowed_ops` ∪ `_QLIB_OPS`）
6. **AST 解析**：禁止 `import`、下划线属性访问（`._xxx`）
7. **look-ahead 防护**：AST 检测 `Ref(..., 负常量)`，命中即拒

**前向收益标签例外**：标签表达式 `Ref($close, -1) / $close - 1` 用未来收益作预测目标是正确的，仅在 `load_label` 中使用，**不经过因子校验**。

### 1.5 防前视检查（`check_lookahead`）

```python
def check_lookahead(expr: str) -> None:
    """仅检查负数 Ref（look-ahead bias），不做白名单校验。"""
```

- 在 `load_factor_values` 执行入口做防御性检查
- 即便表达式绕过创建时的完整校验（`skip_validation=True`），也保证不会加载未来数据
- 非标准表达式（AutoML/TextSentiment 占位符）语法解析失败时直接放行

---

## 二、因子分类

`Factor.category` 字段取值：

| category | 来源 | 表达式形态 | 入库方式 |
|----------|------|-----------|----------|
| `builtin` | 内置因子种子 | 标准 qlib 表达式 | `seed_builtin_factors`（`POST /factors/seed-builtin`） |
| `alpha158` | QLib Alpha158 基准集 | 标准 qlib 表达式 | `seed_alpha158`（`POST /factors/seed-alpha158`） |
| `llm` | LLM 生成 | 标准 qlib 表达式（沙箱校验） | `mine_with_llm` 自动入库 |
| `symbolic` | 符号回归/遗传规划 | 翻译自 gplearn 程序 | `mine_with_symbolic` 自动入库 |
| `text` | 文本情绪因子 | `TextSentiment(...)` 占位符（不支持实时计算） | `mine_with_text` 自动入库 |
| `automl` | AutoML 组合因子 | `AutoML(method, task_id)` 占位符 | `mine_with_automl` 自动入库 |

**特殊表达式**：
- `AutoML(lightgbm, task_id)` / `AutoML(linear, task_id)`：`load_factor_values` 拦截后加载 `data/models/automl/{task_id}.pkl` bundle，重建基础特征并模型预测
- `TextSentiment(...)`：qlib 未注册算子，`load_factor_values` 直接抛 `ValueError`，需重新挖掘预计算值

**因子状态**：`active` / `disabled`（删除即 `disable_factor` 置 disabled）/ `verified`（`auto-import` 达标标记）。

---

## 三、因子评价（最关键的一节）

### 3.1 评价方式对比（v2.4.0 大改造）

| 函数 | 用途 | 时机 |
|------|------|------|
| `evaluate_factor(expr, start, end, horizon=5)` | **单因子单点评价**（旧版） | 兼容旧 API |
| `evaluate_factor_with_validation(...)` | **多维验证**（新版 v2.4.0+） | 挖掘时筛选 |
| `deep_analyze_factor(...)` | **深度分析**（一次性聚合） | UI 详情页 |

### 3.2 多维验证（v2.4.0+，默认走这个）

**核心思想**：单一 IC 容易过拟合。新因子必须通过多道检验：

```
1. 样本分割（按日期） ──►  60% 训练 + 20% 验证 + 20% 测试
2. 多段 IC 计算 ───────►  分别在 train/valid/test 上算日 IC 序列
3. 滚动 IC 统计 ───────►  60 日滚动均值/标准差/正占比
4. 统计显著性 t-test ──►  valid 段 IC 是否显著非零
5. 多样性检测 ─────────►  与已有因子 IC 时序相关性 < 0.8
6. 综合筛选 ───────────►  多条件 AND：valid_ic > 0.03 + stability > 0.5 + positive_ratio > 55% + decay > -0.01
```

**代码入口**：`backend/app/services/quant/factor_validator.py`

**主要函数**：
- `evaluate_factor_with_validation(factor_expr, start, end, universe, horizon=5, ...)`
- 返回值含：`valid_ic`、`valid_icir`、`test_ic`、`passed`、`fail_reasons`、`rolling_stats`、`significance`、`is_duplicate`、`sample_splits`

**筛选阈值**（在 `config.yaml` 配置）：
```yaml
mining:
  llm:
    ic_threshold: 0.03           # valid IC 绝对值阈值
    eval_horizon: 5              # 预测周期（5 日前向收益）
    significance_alpha: 0.05     # t-test 显著性水平
    stability_threshold: 0.5     # IC/IC_std（稳定性）
    positive_ratio_threshold: 0.55  # IC > 0 占比
    decay_threshold: -0.01       # 衰减阈值（前后半段 IC 差）
    diversity_threshold: 0.8     # 因子多样性（IC 相关性）
```

### 3.3 数据加载

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

**v2.4.0 改造**：默认标签改为 `Ref($close, -{horizon}) / $close - 1`（horizon 日前向收益），与 LLM 提示的"预测未来 5 日收益"对齐。

### 3.4 IC（信息系数）

```python
def compute_ic(factor_df: pd.DataFrame, label_df: pd.DataFrame) -> dict:
    """计算 IC / RankIC / ICIR / IR。"""
```

| 指标 | 计算方式 | 含义 |
|------|---------|------|
| **IC** | 每日截面 Pearson 相关 `factor.corr(label)` 的均值 | 因子预测能力的线性度量 |
| **RankIC** | 每日截面 Spearman 相关的均值 | 排名预测能力（更稳健） |
| **ICIR** | `IC 均值 / IC 标准差` | IC 的稳定性（信息比率） |
| **IR** | `RankIC 均值 / RankIC 标准差` | RankIC 的稳定性 |
| `n_days` | 有效截面天数 | 数据量 |

> 每日截面要求 `len(g) >= 2`，否则记 NaN 并 dropna。结果 `round(., 4)`。

### 3.5 换手率

```python
def compute_turnover(factor_df: pd.DataFrame) -> float:
    """每日取 topk（config.quant.topk，默认 50），计算与前一日持仓重合度，
    turnover = 1 - overlap / len(prev)。返回日均换手。"""
```

**含义**：换手率高 → 因子不稳定，交易成本高；换手率低 → 因子稳定，实用性强。

### 3.6 衰减分析（decay）

```python
def compute_decay(factor_df: pd.DataFrame, label_df: pd.DataFrame, max_lag: int = 10) -> dict:
    """因子与未来 1~max_lag 日收益的 IC 序列。
    一次查询 $close 后本地 shift 计算各 lag 前向收益，避免 N 次 qlib IO。"""
```

返回 `{lag: ic}`。`get_factor_decay` 进一步计算：
- **半衰期** `half_life`：IC 衰减到首日一半所需期数
- **有效期** `effective_period`：IC 绝对值 ≥ 0.02 的最后期数

### 3.7 分层回测（`compute_quantile_returns`）

```python
def compute_quantile_returns(
    factor_df, return_df, n_groups: int = 5,
    factor_col="factor", return_col="label",
) -> dict:
```

- 每个截面按因子值 `pd.qcut` 分 `n_groups` 组（重复值失败时降级用 `rank`）
- 输出：
  - `group_returns` — 各组日收益
  - `group_nav` — 各组累计净值
  - `group_stats` — 各组年化、夏普、天数
  - `long_short_returns` — 最高组 - 最低组（多空对冲）
  - `long_short_nav` — 多空累计净值
  - `monotonicity_score` — 组号与组均收益的 Spearman 相关（越接近 1 越好）

### 3.8 因子深度分析（`deep_analyze_factor`）

**端点**：`GET /factors/{id}/deep-analysis`

一次性聚合所有分析，走 IO 线程池（`run_io_cpu`，因为 qlib 释放 GIL）：

```python
def deep_analyze_factor(
    factor_expr, start, end, universe=None,
    horizon: int = 5, n_groups: int = 5, ic_window: int = 60,
) -> dict:
```

**返回结构**：
- `config`：参数
- `summary`：`ic_mean`/`ic_std`/`icir`/`t_stat`/`p_value`/`significant`/`avg_turnover`/`annual_turnover`/`long_short_annual_return`/`monotonicity`
- `ic_distribution`：分箱统计（mean/std/skew/positive_ratio，需 scipy）
- `ic_timeseries`：每日 IC + 滚动均线（窗口 `ic_window`）
- `quantile_returns`：按 horizon 调仓的分层累计净值
- `turnover_curve`：多头组换手率时序
- `decay`：`{lags, ic_by_lag}`

**IC 显著性**（`compute_ic_significance`）：双尾 t 检验，`p_value < 0.05` 为显著；**未经 Newey-West 自相关调整**。

> label 用 `Ref($close, -horizon)/$close - 1`（horizon 周期前向收益），区别于默认 1 日标签。

### 3.9 因子协同性评估

> **当前状态**：相关性矩阵 + 增量 IC 未实现。现有协同性能力：
> - **因子对比**（`compare_factors`）：返回多因子 IC 指标对比、衰减对比（`decay_comparison`）、IC 时序对比（`ic_timeseries`）
> - **正交化**（`orthogonalize.gram_schmidt_orthogonalize`）：按 IC 绝对值降序做 Gram-Schmidt 截面正交化，降低共线性
> - **中性化**（`neutralize`）：`market_cap` / `industry`（行业+市值），对比中性化前后 IC
>
> 完整相关性矩阵与增量 IC 已列入 TODO。

---

## 四、因子加权

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

---

## 五、AI 因子挖掘

四种挖掘方式，统一经 `_safe_run_task` 包装（信号量限流 `task.max_concurrent`、超时分级、异常兜底标 failed）。

### 5.1 LLM 挖掘（最常用）

```python
async def mine_with_llm(task_id: int, n_candidates: int = None) -> dict:
async def mine_with_llm_iterative(task_id: int, n_rounds: int = 3, n_candidates: int = None) -> dict:
```

**端点**：`POST /api/v1/mining/llm?n_candidates=10&n_rounds=1`（3/min 限流）

**流程**：
1. 用 `_USER_PROMPT_TEMPLATE` 构造提示（含算子/字段/语法示例/look-ahead 警告）
2. `ProviderRouter.route_request` 调 LLM（三级故障转移），强制返回 JSON
3. **逐候选**：
   - `validate_expression` 沙箱校验
   - `evaluate_factor_with_validation` 多维验证（**v2.4.0+**）
   - 通过验证的因子入库（category=llm）
4. 更新任务统计：`candidates_generated`/`candidates_passed`/`best_ic`/`result_factor_ids`

**迭代挖掘**（`n_rounds > 1`）：每轮生成 → 校验 → 多维验证 → `_build_feedback_prompt` 反馈给 LLM 逐轮改进。

**v2.4.0 关键改造**：
- **不再只算全样本 IC**，而是 valid 段滚动 IC + t-test + 多样性
- 通过验证的因子入库（之前通过 IC 阈值即可）
- GPU 自动检测（v2.4.0+）

**超时策略**：不限时，依赖内部原子超时（provider httpx timeout + `eval_timeout_seconds`）+ `llm_hard_limit_seconds`（默认 7200s）硬上限兜底。

### 5.2 符号回归（`mine_with_symbolic`）

```python
async def mine_with_symbolic(task_id: int) -> dict:
```

**端点**：`POST /api/v1/mining/symbolic`

**流程**（gplearn 遗传编程）：
1. 12 个基础特征作为终端（`_BASE_FEATURES`：mom_5/mom_20/vol_20/vol_60/turn_5/turn_20/vratio/amp_20/ma_div_20/ma_div_60/high_dd_20/rsi_20）
2. 扩展函数集（add/sub/mul/div/log/abs/sign/max/min/if）
3. `SymbolicRegressor` 演化（population=1000, generations=30, tournament_size=20, parsimony_coefficient=0.001）
4. `_translate_program` 将最优程序翻译为 qlib 表达式（add→Add, Xi→子表达式）
5. 沙箱校验 + **多维验证** + 入库（category=symbolic）

**v2.4.0+**：有 GPU 时 `n_jobs=-1`（LightGBM 训练用 GPU 加速）。

### 5.3 AutoML（`mine_with_automl`）

```python
async def mine_with_automl(task_id: int, factor_ids: list[int], method: str = None) -> dict:
```

**端点**：`POST /api/v1/mining/automl`

**流程**：
1. 加载指定基础因子值（AutoML bundle 丢失/文本算子不可用时跳过该因子）
2. 截面标准化后训练 `lightgbm`/`linear` 模型预测前向收益
3. `time_series_cv_eval` 时序交叉验证（n_splits=5）
4. `joblib` 持久化到 `data/models/automl/{task_id}.pkl`（含 `feature_names`/`factor_expressions` bundle）
5. SHAP 特征重要性写入任务结果
6. 入库 `AutoML(method, task_id)` 占位符因子（category=automl）

**GPU 加速（v2.4.0+）**：训练时自动检测 GPU，有则 LightGBM `device='gpu'`，CV 也走 GPU。

### 5.4 文本因子（`mine_with_text`）

```python
async def mine_with_text(task_id: int, codes: list[str] = None) -> dict:
```

**端点**：`POST /api/v1/mining/text`

**流程**：
1. `_fetch_news_for_universe` 拉取新闻（默认 universe 前 30 只，`max_news_per_day=50`）
2. `_classify_sentiment` LLM 批量情绪分类（batch_size=20，返回 score 1/0/-1）
3. 聚合为每日截面情绪因子
4. IC 评价 + 入库（category=text，表达式为 `TextSentiment(...)` 占位符）

> ⚠️ 文本因子表达式为占位符，`load_factor_values` 不支持实时计算，不可直接用于 qlib 回测/深度分析。

### 5.5 防前视检查

所有挖掘路径在入库前经 `validate_expression`（含 AST 负数 Ref 检测），执行入口再经 `check_lookahead` 双重防护，杜绝 look-ahead bias。

---

## 六、策略回测

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

## 七、因子库 CRUD 与评价调度

`backend/app/services/factor/library.py`：

| 函数 | 说明 |
|------|------|
| `list_factors(category, status, sort_by, limit, offset)` | 列表，sort_by ∈ ic/rank_ic/icir/created_at |
| `get_factor(factor_id)` | 详情 |
| `add_factor(name, expression, category, description, source_task_id, skip_validation)` | 新增（默认带沙箱校验） |
| `disable_factor(factor_id)` | 软删除（status=disabled） |
| `update_factor_metrics(factor_id, metrics)` | 写回 IC/RankIC/ICIR/IR/turnover/decay |
| `evaluate_factor_by_id(factor_id, start, end)` | 评价库中因子，走 `run_io_cpu` 线程池 |
| `seed_builtin_factors` / `seed_alpha158` | 种子内置因子 |

**Factor 模型字段**：
```
id / name / expression / category / description
ic / rank_ic / icir / ir / turnover / decay(JSON)
eval_start / eval_end / evaluated_at
status / source_task_id / created_at
```

### 7.1 Alpha158 批量评价（v2.5.x 重点优化）

158 个标准因子的批量评价是性能瓶颈，经过 3 个版本持续优化：

**v2.5.1** — 预加载 + 线程池：
- `lru_cache` 缓存 `_load_instruments`（股票池查询从 158 次降到 1 次）
- 预加载 `preloaded_label_df` 和 `preloaded_close_df`
- 走 `run_io_cpu` 线程池（qlib 释放 GIL）
- 批量写入（每 20 条一次 commit）

**v2.5.2** — 实时写入：
- 每个因子算完立即 commit
- 进度回调每次完成都触发（之前每 10 次）

**v2.5.3** — Queue 解耦（关键修复）：
- **根因**：之前 16 并发 + 每完成立即 await DB commit，pool_size=10 的连接被抢光，后续 commit 等连接回收 100+ 秒
- **修复**：`asyncio.Queue` 把评价完成结果传给单 DB writer 协程串行 commit
- `max_concurrent` 从 16 降到 4（避免 qlib 多线程锁竞争）

**性能结果**：
- 修复前：~10+ 分钟（每 8 个卡 100s）
- 修复后：~20 秒（每个因子 ~120ms）

---

## 八、相关 API 速查

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

详见 [API_REFERENCE.md](API_REFERENCE.md)。

---

## 九、已知问题与 TODO

- 因子协同性评估（相关性矩阵 + 增量 IC）未实现，仅有 IC 对比/衰减对比/正交化/中性化
- 文本因子（`TextSentiment`）与 AutoML（`AutoML(...)`）为占位符表达式，不可直接用于 qlib 回测/深度分析；策略回测会跳过此类因子
- IC 显著性 t 检验未经 Newey-West 自相关调整
- Alpha158 评价并发参数需根据服务器 CPU/IO 能力调优