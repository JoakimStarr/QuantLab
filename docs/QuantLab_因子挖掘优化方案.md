---
title: 因子挖掘优化方案
slug: quantlab-因子挖掘优化方案
order: 2
group: 优化方案
summary: 因子挖掘专项优化：样本分割 + 多维验证 + GPU 检测 + 实时写入
---

# QuantLab 因子挖掘优化方案

> **状态：已实施（2026-08-02）** — 本方案结论已落地为代码改动，详见 git log 与测试套件。


> 生成日期：2026-08-01
> 基于项目审视报告和因子挖掘效率问题的深入分析

---

## 一、问题根因分析

### 1.1 "跑了100多个，一个都没有有效的" — 根因诊断

经过对因子挖掘全流程的逐层分析，发现**多层失效叠加**：

```
LLM Prompt → 生成表达式 → 沙箱校验 → IC评价 → 阈值筛选 → 入库
                              ↓
                    失效层叠在这里
```

#### 🔴 根本原因 1：没有样本分割（最致命）

[llm_factor.py](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L67-L158) 中 `mine_with_llm()` 的流程：

1. LLM 生成 10 个候选因子
2. 全部在 **2020-01-01 ~ 2024-12-31 全量数据**上评价 IC
3. IC >= 0.03 的因子入库

**问题**：评价和筛选用同一段数据，等价于在测试集上调参。对于 csi300（~300 只股票），随机因子的 IC 标准差约为 1/√300 ≈ 0.058。阈值 0.03 仅 0.5 个标准差——**10 个随机因子中期望有 3-4 个通过阈值**，100 个中期望有 30-40 个。但这些都是**噪声**，实际回测时全部失效。

#### 🟡 根本原因 2：Label 错配

[factor_eval.py](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_eval.py#L17) 中默认 label 是 `Ref($close, -1) / $close - 1`（1 日收益），但 [prompt](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L31) 说"预测未来5日股票收益"。**LLM 按 5 日收益设计因子，但用 1 日收益评价**，IC 天然偏低。

#### 🟡 根本原因 3：IC 阈值缺乏统计显著性

| 股票池 | 样本数 | 随机 IC 标准差 | 阈值 0.03 对应的显著性 |
|--------|--------|---------------|----------------------|
| csi300 | ~300 | 0.058 | z=0.52, p=0.30（不显著） |
| csi500 | ~500 | 0.045 | z=0.67, p=0.25（不显著） |
| csi800 | ~800 | 0.035 | z=0.85, p=0.20（不显著） |

阈值 0.03 在 csi300 上完全不显著，只是噪声水平。

#### 🟡 根本原因 4：LLM 生成质量不可控

LLM 生成的是"猜"表达式，不是数据驱动的搜索。对于 qlib 这种 DSL，LLM 的理解有限，生成的表达式往往：
- 语法正确但语义无意义（如 `Rank($close) * 1 / 1`）
- 过度复杂（深层嵌套，实际只等价于简单表达式）
- 高度相似（多轮生成的都是近亲变体）

#### 🔵 辅助原因 5：算子空间贫乏

只允许价格/成交量算子，没有：
- 基本面（PE/PB/ROE/营收增长）
- 技术指标（MACD/KDJ/布林带）
- 行业/风格暴露
- 另类数据

---

### 1.2 失效链路总结

```
LLM 生成表达式（猜）
    ↓
沙箱校验（通过，只检查语法）
    ↓
全量数据 IC 评价（IC=0.035，达到阈值）
    ↓
✅ 入库（但 IC 是噪声，非真实预测力）
    ↓
实际回测 → 失效
```

**每一层都没有阻挡噪声因子**：沙箱只检查语法，IC 评价不做样本外验证，阈值不统计显著。

---

## 二、优化方案总览

### 方案架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        优化后的因子挖掘流程                          │
│                                                                   │
│  LLM/GP生成 → 沙箱校验 → 样本分割 → 多维度评价 → 统计检验 → 稳健性验证 → 入库 │
│                           ↓                                        │
│                    训练集(60%) → 截面IC                             │
│                    验证集(20%) → 滚动IC + ICIR                     │
│                    测试集(20%) → 最终验证（不入库，仅记录）           │
│                                                                   │
│  新增模块：                                                       │
│  ├── 样本分割器 (SampleSplitter)                                   │
│  ├── 滚动IC引擎 (RollingICEvaluator)                              │
│  ├── 统计显著性检验 (StatisticalSignificance)                      │
│  ├── 因子多样性检测 (DiversityChecker)                             │
│  └── 因子稳健性验证 (RobustnessValidator)                          │
└─────────────────────────────────────────────────────────────────┘
```

### 优化优先级

| 优先级 | 优化项 | 预期效果 | 工作量 |
|--------|--------|----------|--------|
| P0 | **样本分割 + 统计显著性检验** | 消除噪声因子，筛选门槛提高 10 倍 | 2-3 天 |
| P0 | **Label 修复（5 日收益）** | IC 匹配 LLM 目标，提升 2-3 倍 | 0.5 天 |
| P1 | **滚动 IC 评价 + 时序稳健性** | 过滤"运气好"的因子 | 1-2 天 |
| P1 | **因子多样性约束** | 减少冗余因子堆积 | 1 天 |
| P1 | **前端挖掘进度优化** | 用户体验提升 | 2-3 天 |
| P2 | **算子空间扩展 + 基础因子库** | 提升因子质量上限 | 3-5 天 |
| P2 | **IC 缓存上限保护** | 防 OOM | 0.5 天 |
| P3 | **执行器统一（线程池替代进程池）** | 小任务性能提升 | 1 天 |

---

## 三、详细优化方案

### 3.1 P0：样本分割 + 统计显著性检验（最核心）

#### 现状

[factor_eval.py](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_eval.py#L289-L303) 的 `evaluate_factor()` 在完整区间上计算单一 IC，没有任何样本分割。

#### 改造方案

**新建文件**：[backend/app/services/quant/factor_validator.py](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_validator.py)

```python
"""因子验证器：样本分割 + 滚动IC + 统计检验 + 稳健性验证。"""

class SampleSplitter:
    """时间序列样本分割器。
    
    策略：按日期分割，保持截面完整性。
    - train: 60%（前段，用于因子发现）
    - valid: 20%（中段，用于IC筛选和阈值判断）
    - test: 20%（后段，用于最终验证，不进筛选条件）
    """
    
    @staticmethod
    def split(dates, train_ratio=0.6, valid_ratio=0.2):
        n = len(dates)
        train_end = int(n * train_ratio)
        valid_end = int(n * (train_ratio + valid_ratio))
        return {
            "train": dates[:train_end],
            "valid": dates[train_end:valid_end],
            "test": dates[valid_end:],
        }


class FactorValidator:
    """因子多维验证器。
    
    评价维度：
    1. 截面 IC（全样本 + 分段）
    2. 滚动 IC（60 日窗口，看稳定性）
    3. 统计显著性（t-test + 调整后 p-value）
    4. IC 衰减曲线
    5. 分组收益单调性
    """
    
    def validate(self, expr, start, end):
        # 1. 样本分割
        # 2. 逐段计算 IC
        # 3. 滚动 IC 时序
        # 4. 统计检验
        # 5. 综合评分
        pass
```

**关键设计**：

1. **valid_ic 作为筛选指标**：LLM 挖掘中，候选因子只使用 **valid 段 IC** 做筛选，train 段 IC 仅做参考，test 段 IC 仅记录（不参与筛选）
2. **统计显著性**：对 valid 段 IC 序列做 t-test（H0: mean IC = 0），p < 0.05 才算有效
3. **滚动 IC 稳定性**：valid 段滚动 60 日 IC 的标准差 / 均值 < 2.0 才算稳定

#### 修改文件

| 文件 | 修改内容 |
|------|----------|
| `backend/app/services/quant/factor_validator.py` | 新建 |
| `backend/app/services/quant/factor_eval.py` | 新增 `evaluate_factor_with_validation()` |
| `backend/app/services/mining/llm_factor.py` | 改用 `evaluate_factor_with_validation()`，用 valid_ic 筛选 |
| `backend/app/services/mining/symbolic.py` | 改用 valid_ic 筛选 |

### 3.2 P0：Label 修复

#### 现状

[factor_eval.py#L17](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_eval.py#L17) 中 `_DEFAULT_LABEL = "Ref($close, -1) / $close - 1"`（1 日收益）。

[llm_factor.py#L31](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L31) 中 prompt 说"预测未来5日股票收益"。

#### 修改

在 [factor_eval.py](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_eval.py) 中增加 `horizon` 参数支持：

```python
def evaluate_factor(factor_expr: str, start: str, end: str, 
                    universe: str = None, horizon: int = 5) -> dict:
    """完整因子评价，支持多周期标签。"""
    label = f"Ref($close, -{horizon}) / $close - 1"
    factor_df = load_factor_values(factor_expr, start, end, universe)
    label_df = load_label(start, end, label_expr=label, universe=universe)
    ...
```

同时在 [config.py](file:///home/joakim/Project/QuantLab/backend/app/core/config.py) 的 `MiningSettings` 中增加 `eval_horizon: int = 5` 配置项。

#### 修改文件

| 文件 | 修改内容 |
|------|----------|
| `backend/app/services/quant/factor_eval.py` | `evaluate_factor()` 增加 `horizon` 参数 |
| `backend/app/core/config.py` | `MiningSettings` 增加 `eval_horizon` |
| `backend/app/services/mining/llm_factor.py` | 调用时传入 `horizon` |
| `backend/app/services/factor/library.py` | `evaluate_factor_by_id()` 传入 `horizon` |

### 3.3 P1：滚动 IC 评价 + 时序稳健性

#### 现状

当前 IC 是全样本期均值，没有时间维度。一个因子可能在 2020-2021 年 IC=0.08，2022-2024 年 IC=-0.02，但全期均值 IC=0.03 仍然"达标"。

#### 改造

在 `factor_validator.py` 中增加 `RollingICEvaluator`：

```python
class RollingICEvaluator:
    """滚动 IC 评价器。
    
    功能：
    1. 计算 60 日滚动窗口的 IC 序列
    2. 输出 IC 均值、标准差、正负占比
    3. 检测 IC 衰减（后半段 vs 前半段）
    4. 稳定性评分：IC 均值 / IC 标准差（越大越好）
    """
    
    @staticmethod
    def evaluate(factor_df, label_df, window=60):
        # 每日截面 IC → IC 时序
        daily_ic = compute_daily_ic(factor_df, label_df)
        # 滚动均值和标准差
        rolling_mean = daily_ic.rolling(window).mean()
        rolling_std = daily_ic.rolling(window).std()
        # 稳定性指标
        ic_mean = daily_ic.mean()
        ic_std = daily_ic.std()
        stability = ic_mean / ic_std if ic_std > 0 else 0
        # 后半段 vs 前半段
        mid = len(daily_ic) // 2
        ic_first_half = daily_ic[:mid].mean()
        ic_second_half = daily_ic[mid:].mean()
        decay = ic_second_half - ic_first_half  # 负值=衰减
        
        return {
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "stability": stability,  # 信息比率
            "positive_ratio": (daily_ic > 0).mean(),
            "ic_first_half": ic_first_half,
            "ic_second_half": ic_second_half,
            "decay": decay,
            "ic_series": daily_ic.tolist(),
        }
```

**筛选条件**（加入后，因子需同时满足）：
- valid_ic > 0.03 AND
- stability > 0.5 AND
- positive_ratio > 0.55 AND
- decay > -0.01（不显著衰减）

### 3.4 P1：因子多样性约束

#### 现状

[llm_factor.py](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L206-L213) 中的 `_is_duplicate()` 使用字符串相似度去重，不够精确。

#### 改造

在 `factor_validator.py` 中增加 `DiversityChecker`：

```python
class DiversityChecker:
    """因子多样性检测器。
    
    策略：
    1. 表达式标准化：常数折叠（Rank($close) * 1 → Rank($close)）
    2. 表达式树结构比较：相同结构不同参数视为类似
    3. 相关性检测：与已有因子库的 IC 相关性 > 0.8 视为冗余
    """
    
    @staticmethod
    def normalize(expr):
        """表达式标准化：简化常数运算、重排参数顺序。"""
        pass
    
    @staticmethod
    def tree_distance(expr1, expr2):
        """基于表达式树的编辑距离。"""
        pass
    
    @staticmethod
    def correlation_with_existing(expr, existing_exprs, start, end):
        """与已有因子的 IC 时序相关性。"""
        pass
```

**筛选条件**：
- 与已有因子库 IC 相关性 < 0.8
- 表达式树距离 > 阈值

### 3.5 P1：前端挖掘进度优化

#### 现状

[Mining.vue](file:///home/joakim/Project/QuantLab/frontend/src/views/quant/Mining.vue) 使用 5 秒轮询查看任务状态，没有进度条或预估剩余时间。

#### 改造

1. **WebSocket 推送任务进度**：在 `websocket_manager.py` 中增加 `task_progress` 事件类型，挖掘任务各阶段（LLM 调用、IC 评价、入库）推送进度
2. **前端进度条**：在 `Mining.vue` 中增加进度条组件，显示阶段和进度百分比
3. **轮询降级**：当 WebSocket 离线时，轮询间隔从 5 秒自适应到 2 秒

#### 修改文件

| 文件 | 修改内容 |
|------|----------|
| `backend/app/services/mining/llm_factor.py` | 各阶段通过 WebSocket 广播进度 |
| `backend/app/core/websocket_manager.py` | 增加 `task_progress` 事件类型 |
| `frontend/src/views/quant/Mining.vue` | 增加进度条组件 |
| `frontend/src/stores/mining.js` | 新建 store，管理 WebSocket 任务进度 |

### 3.6 P2：算子空间扩展 + 基础因子库

#### 现状

当前只支持价格/成交量算子，[allowed_ops](file:///home/joakim/Project/QuantLab/backend/app/core/config.py#L158-L180) 中没有基本面/技术指标。

#### 改造

1. **扩展 qlib 算子白名单**：在 [expression.py](file:///home/joakim/Project/QuantLab/backend/app/services/factor/expression.py) 的 `_QLIB_OPS` 中增加：
   - `ROC`, `RSI`, `MACD`, `KDJ`, `BOLL`, `ATR`, `OBV` 等常见技术指标
   - `PE`, `PB`, `ROE`, `NET_PROFIT_MARGIN` 等基本面字段（需先同步数据）

2. **增加基础因子种子库**：在 [factor API](file:///home/joakim/Project/QuantLab/backend/app/api/factor.py) 中增加一批经过验证的已知有效因子作为种子，LLM 可以在此基础上变异

3. **增加模板的多样性**：在 [mining_templates.py](file:///home/joakim/Project/QuantLab/backend/app/services/mining/mining_templates.py) 中增加：
   - 质量因子模板（ROE/毛利率）
   - 成长因子模板（营收增长/利润增长）
   - 估值因子模板（PE/PB/PS）

### 3.7 P2：IC 缓存上限保护

#### 现状

[llm_factor.py#L25](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L25) 中 `_ic_cache: dict[str, dict] = {}` 无大小限制。

#### 改造

```python
from collections import OrderedDict

_IC_CACHE_MAX = 1024
_ic_cache: OrderedDict[str, dict] = OrderedDict()

def _ic_cache_put(key: str, value: dict) -> None:
    _ic_cache[key] = value
    _ic_cache.move_to_end(key)
    if len(_ic_cache) > _IC_CACHE_MAX:
        _ic_cache.popitem(last=False)
```

### 3.8 P3：执行器统一（线程池替代进程池）

#### 现状

[executor.py](file:///home/joakim/Project/QuantLab/backend/app/core/executor.py#L35-L42) 中 `run_cpu()` 使用 `ProcessPoolExecutor`，但因子评价主要调用 qlib 的 C 扩展（会释放 GIL），用线程池就够。

#### 改造

将因子评价改为使用 `IO 线程池`：

```python
# 在 run_cpu 中增加一个判断：如果是因子评价，走线程池
# 或者直接拆分：
# - run_cpu: 纯 CPU 计算（走进程池）
# - run_io: IO 密集型（走线程池）
# 因子评价走 run_io
```

---

## 四、实施计划

### 4.1 第一阶段（P0，预计 3-4 天）

| 任务 | 时间 | 产出 |
|------|------|------|
| 新建 `factor_validator.py`（样本分割 + 统计显著性） | 2 天 | 因子验证器模块 |
| 修复 Label 为 5 日收益 | 0.5 天 | 所有调用方同步修改 |
| 接入 `factor_validator` 到 LLM 挖掘 | 1 天 | LLM 挖掘使用 valid_ic 筛选 |
| 接入 `factor_validator` 到符号回归 | 0.5 天 | 符号回归使用 valid_ic 筛选 |

### 4.2 第二阶段（P1，预计 3-4 天）

| 任务 | 时间 | 产出 |
|------|------|------|
| 滚动 IC 评价 + 时序稳健性 | 1.5 天 | 稳定因子筛选 |
| 因子多样性约束 | 1 天 | 表达式去重 + 相关性去重 |
| WebSocket 任务进度推送 | 1.5 天 | 前端进度条 + 后端广播 |

### 4.3 第三阶段（P2，预计 3-5 天）

| 任务 | 时间 | 产出 |
|------|------|------|
| 算子空间扩展 | 1.5 天 | 更多算子 + 技术指标 |
| 基础因子种子库 | 1 天 | 已知有效因子入库 |
| 模板多样化 | 0.5 天 | 质量/成长/估值模板 |
| IC 缓存上限保护 | 0.5 天 | 防 OOM |

### 4.4 第四阶段（P3，持续优化）

| 任务 | 时间 | 产出 |
|------|------|------|
| 执行器统一 | 1 天 | 性能优化 |
| 持久化因子评价结果 | 1 天 | 结果可复现 |
| 监控指标补充 | 1 天 | 可观测性 |

---

## 五、预期效果

### 5.1 因子质量提升

| 指标 | 当前 | 优化后（预期） |
|------|------|---------------|
| 通过率 | 30-40% 的随机因子通过 | < 5% 的因子通过，但通过的因子有效 |
| 样本外 IC | 不验证（实际为 0） | > 0.02 |
| 统计显著性 | 未检验 | p < 0.05 |
| 滚动 IC 稳定性 | 未检验 | stability > 0.5 |
| 因子冗余度 | 高度重复 | 表达式树距离 > 阈值 |

### 5.2 用户体验提升

| 场景 | 当前 | 优化后 |
|------|------|--------|
| 挖掘任务提交 | 无反馈，等 5 秒轮询 | WebSocket 实时推送进度 |
| 挖掘完成 | 只显示"完成" | 显示通过率、最佳 IC、时间分布 |
| 因子库 | 大量无效因子 | 少而精，每个因子都有验证数据 |

---

## 六、关键代码改动摘要

### 6.1 新建文件

| 文件路径 | 用途 |
|----------|------|
| `backend/app/services/quant/factor_validator.py` | 因子验证器（样本分割、滚动 IC、统计检验、多样性） |
| `frontend/src/stores/mining.js` | 挖掘任务进度管理的 Pinia store |

### 6.2 修改文件

| 文件路径 | 改动内容 |
|----------|----------|
| `backend/app/services/quant/factor_eval.py` | `evaluate_factor()` 增加 `horizon` 参数，新增 `evaluate_factor_with_validation()` |
| `backend/app/services/mining/llm_factor.py` | 改用 `factor_validator`，用 valid_ic 筛选，增加 WebSocket 进度广播 |
| `backend/app/services/mining/symbolic.py` | 改用 `factor_validator`，用 valid_ic 筛选 |
| `backend/app/services/factor/library.py` | `evaluate_factor_by_id()` 传入 `horizon` |
| `backend/app/core/config.py` | `MiningSettings` 增加 `eval_horizon` |
| `backend/app/core/websocket_manager.py` | 增加 `task_progress` 事件类型 |
| `backend/app/services/factor/expression.py` | 扩展 `_QLIB_OPS` 白名单 |
| `backend/app/services/mining/mining_templates.py` | 增加质量/成长/估值模板 |
| `frontend/src/views/quant/Mining.vue` | 增加进度条组件，WebSocket 监听 |
| `frontend/src/api/mining.js` | 新增 WebSocket 连接方法 |

---

## 七、验证方法

### 7.1 单元测试

新建 `backend/tests/test_factor_validator.py`，覆盖：

```
test_sample_splitter:
  - 日期分割正确
  - 各段保持截面完整性
  
test_rolling_ic:
  - 稳定因子 vs 不稳定因子的区分度
  
test_statistical_significance:
  - 随机因子的 p-value 应 > 0.05
  - 已知有效因子的 p-value 应 < 0.05

test_diversity_checker:
  - 表达式标准化
  - 相似表达式检测
  - 相关性去重
```

### 7.2 端到端验证

1. 启动 LLM 挖掘（n_candidates=10, n_rounds=1）
2. 验证：通过因子数 < 3（之前 30-40% 通过率 → 现应 < 30%）
3. 对通过的因子做回测验证
4. 对比优化前后的 hit rate（有效因子占比）

---

## 八、风险与注意事项

### 8.1 风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 样本分割后数据量不足 | 低 | 评价不准确 | 自动检测数据量，不足时用更短评价期 |
| 统计显著性过严 | 中 | 无因子通过 | 提供可配置的显著性水平 |
| 滚动 IC 计算耗时增加 | 中 | 挖掘变慢 | 滚动 IC 和全样本 IC 并行计算 |
| 多样性约束误杀 | 低 | 丢失好因子 | 多样性约束可配置，提供手动 override |

### 8.2 注意事项

- **向后兼容**：历史因子库中已入库的因子不重新评价，只对新挖掘的因子使用新流程
- **配置可调**：所有阈值（valid_ic_threshold、stability_threshold、p_value_threshold）都可配置
- **渐进式上线**：先加样本分割 + 统计显著性（P0），观察效果后再加滚动 IC（P1）