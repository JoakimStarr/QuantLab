# QuantLab 完整优化方案

> 生成日期：2026-08-01
> 版本：v1.0
> 基于项目审视分析报告提炼的完整优化路线图

---

## 一、优化路线图总览

### 1.1 问题分类与优先级

| 优先级 | 领域 | 问题数 | 总工作量 | 核心收益 |
|--------|------|--------|----------|----------|
| **P0** | 方法论 + 安全 | 4 | 5-7 天 | 因子质量 + 系统安全 |
| **P1** | 性能 + 体验 | 8 | 10-14 天 | 效率 + 用户体验 |
| **P2** | 数据库 + 运维 | 5 | 5-7 天 | 稳定性 + 可维护性 |
| **P3** | 代码质量 + 监控 | 4 | 4-5 天 | 可观测性 + 代码整洁 |

### 1.2 总体时间线

```
第1周  ─── P0 ─────────────────────────────────
         ├── 因子挖掘样本分割 + 统计检验 (3天)
         ├── Label 修复 + 表达式沙箱加固 (1天)
         └── JWT 过期 + 默认凭据 (1天)

第2-3周 ─── P1 ────────────────────────────────
         ├── 因子评价 Pipeline 优化 (3天)
         ├── 滚动 IC + 多样性约束 (2天)
         ├── WebSocket 全面推送 + 前端进度 (3天)
         └── 前端骨架屏 + 错误边界 + 大数据表格 (2天)

第3-4周 ─── P2 ────────────────────────────────
         ├── Docker 部署加固 + CI/CD (2天)
         ├── 数据库归档 + 连接池监控 (2天)
         ├── 日志系统结构化 (1天)
         └── 数据同步增量合并 (2天)

第5周  ─── P3 ────────────────────────────────
         ├── Prometheus 指标补充 (1天)
         ├── 执行器优化 (1天)
         └── 请求 Trace 追踪 (1天)
```

---

## 二、P0 级优化（5-7 天）

### 2.1 因子挖掘样本分割 + 统计显著性检验

**现状**：[factor_eval.py](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_eval.py#L289-L303) 在完整区间上计算单一 IC，无样本分割。

**改造**：新建 `factor_validator.py`，实现 60/20/20 时间序列分割，只使用 valid 段 IC 做筛选，增加 t-test 统计显著性检验。

**详细方案**：见 [QuantLab_因子挖掘优化方案.md](file:///home/joakim/Project/QuantLab/docs/QuantLab_因子挖掘优化方案.md) 第 3.1 节。

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/services/quant/factor_validator.py` | 新建 |
| `backend/app/services/quant/factor_eval.py` | 新增 `evaluate_factor_with_validation()` |
| `backend/app/services/mining/llm_factor.py` | 改用 valid_ic 筛选 |
| `backend/app/services/mining/symbolic.py` | 改用 valid_ic 筛选 |
| `backend/app/services/factor/library.py` | 评价接口传入 horizon |

### 2.2 Label 修复

**现状**：[factor_eval.py](file:///home/joakim/Project/QuantLab/backend/app/services/quant/factor_eval.py#L17) 中 `_DEFAULT_LABEL = "Ref($close, -1) / $close - 1"`（1 日收益），但 [prompt](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L31) 说"预测未来5日股票收益"。

**改造**：`evaluate_factor()` 增加 `horizon` 参数，`MiningSettings` 增加 `eval_horizon: int = 5`。

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/services/quant/factor_eval.py` | `evaluate_factor()` 增加 `horizon` 参数 |
| `backend/app/core/config.py` | `MiningSettings` 增加 `eval_horizon` |

### 2.3 表达式沙箱加固

**现状**：[expression.py](file:///home/joakim/Project/QuantLab/backend/app/services/factor/expression.py#L64-L132) 的 `validate_expression()` 使用 AST 解析 + 白名单校验，但可能有绕过风险。

**改造**：
1. 增加更严格的 AST 节点类型白名单（只允许 `Expression`、`Call`、`Name`、`Constant`、`BinOp`、`UnaryOp`、`Attribute`）
2. 增加表达式执行沙箱（`exec()` 受限环境，用于 qlib 表达式执行前验证）
3. 增加 `eval()` 关键字检测
4. 增加表达式复杂度上限（最大嵌套深度 ≤ 20，节点数 ≤ 100）

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/services/factor/expression.py` | 增加 AST 节点白名单、复杂度上限 |
| `backend/app/core/config.py` | `MiningSettings` 增加 `max_expr_depth`, `max_expr_nodes` |

### 2.4 JWT Token 过期 + 默认凭据加固

**现状**：JWT token 无过期时间，`SECRET_KEY` 和 `ADMIN_PASSWORD` 使用默认值。

**改造**：
1. JWT token 增加 `exp` 声明（默认 24 小时，可配置）
2. 增加 refresh token 机制（7 天有效期）
3. 生产环境强制 `enforce_production_security()` 阻塞启动
4. 增加密码强度检测

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/auth.py` | JWT 增加 `exp`，增设 `ACCESS_TOKEN_EXPIRE_HOURS` |
| `backend/app/core/config.py` | `SecuritySettings` 增加 `access_token_expire_hours` |
| `backend/app/main.py` | 启动时调用 `enforce_production_security()` |

---

## 三、P1 级优化（10-14 天）

### 3.1 因子评价 Pipeline 优化

**现状**：`evaluate_factor()` 需要通过 `executor.run_cpu()` 走进程池，4 个 worker，且每个任务都要序列化参数。

**改造**：
1. **进程池 → 线程池**：因子评价主要调用 qlib C 扩展（释放 GIL），线程池足够。在 `executor.py` 中新增 `run_io_cpu()` 方法，走线程池
2. **qlib 初始化缓存**：`init_qlib()` 在进程/线程间共享，避免每次评价重复初始化
3. **因子值预加载**：LLM 挖掘中，10 个候选因子的评价共用因子值加载（如 `$close`、`$volume` 等基础数据只加载一次）

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/executor.py` | 新增 `run_io_cpu()` 线程池方法 |
| `backend/app/services/quant/qlib_init.py` | 增加 `qlib` 实例缓存，避免重复初始化 |
| `backend/app/services/mining/llm_factor.py` | 批量因子评价预加载基础数据 |
| `backend/app/core/config.py` | `TaskSettings` 增加 `eval_io_workers` |

### 3.2 滚动 IC 评价 + 时序稳健性

**现状**：IC 是全样本均值，没有时间维度。

**改造**：在 `factor_validator.py` 中增加 `RollingICEvaluator`，计算 60 日滚动 IC 序列，输出稳定性指标。

**筛选条件升级**（从单一阈值 → 多条件）：
```
valid_ic > 0.03
AND stability > 0.5
AND positive_ratio > 0.55
AND decay > -0.01
```

**详细方案**：见 [QuantLab_因子挖掘优化方案.md](file:///home/joakim/Project/QuantLab/docs/QuantLab_因子挖掘优化方案.md) 第 3.3 节。

### 3.3 因子多样性约束

**现状**：[llm_factor.py](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L206-L213) 使用字符串相似度去重，不够精确。

**改造**：在 `factor_validator.py` 中增加 `DiversityChecker`：
- 表达式标准化（常数折叠）
- 表达式树结构比较
- 与已有因子库的 IC 时序相关性检测

**筛选条件**：与已有因子库 IC 相关性 < 0.8，表达式树距离 > 阈值。

### 3.4 IC 缓存上限保护

**现状**：[llm_factor.py#L25](file:///home/joakim/Project/QuantLab/backend/app/services/mining/llm_factor.py#L25) 中 `_ic_cache: dict` 无大小限制。

**改造**：改为 `OrderedDict`，上限 1024 条，LRU 淘汰。

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/services/mining/llm_factor.py` | 增加 `_IC_CACHE_MAX = 1024`，改用 `OrderedDict` |

### 3.5 数据同步增量合并

**现状**：[sync_runner.py](file:///home/joakim/Project/QuantLab/backend/app/services/data/sync_runner.py) 中 `chenditc` 全量同步每次下载整个 qlib bin tarball。

**改造**：
1. 增加增量同步策略：检测本地最新交易日，只下载增量数据
2. 同步进度线程安全：`sync_progress.py` 改用 `asyncio.Lock` 或 `ContextVar`，支持多实例

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/services/data/sync_runner.py` | 增加增量同步策略 |
| `backend/app/services/data/sync_progress.py` | 改用 `asyncio.Lock` |

### 3.6 WebSocket 全面推送 + 前端进度

**现状**：前端使用 5 秒/3 秒轮询监控任务状态，WebSocket 仅用于同步完成通知。

**改造**：
1. 后端：挖掘任务各阶段（LLM 调用、IC 评价、入库）通过 WebSocket 广播 `task_progress` 事件
2. 前端：`Mining.vue` 增加进度条组件，显示阶段和百分比
3. 轮询降级：WebSocket 离线时自动切换到轮询

**事件流**：
```
task_progress:
  { type: "task_progress", task_id, phase: "llm_call"|"eval"|"save", 
    progress: 0-100, message: "正在评价因子 5/10" }
```

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/websocket_manager.py` | 增加 `task_progress` 事件类型 |
| `backend/app/services/mining/llm_factor.py` | 各阶段 broadcast 进度 |
| `frontend/src/views/quant/Mining.vue` | 增加进度条组件，WebSocket 监听 |
| `frontend/src/stores/mining.js` | 新建 WebSocket 连接 store |

### 3.7 前端骨架屏 + 加载状态

**现状**：[Dashboard.vue](file:///home/joakim/Project/QuantLab/frontend/src/views/quant/Dashboard.vue) 加载时空白区域，[FactorLibrary.vue](file:///home/joakim/Project/QuantLab/frontend/src/views/quant/FactorLibrary.vue) 骨架屏触发不及时。

**改造**：
1. 所有页面增加 `el-skeleton` 骨架屏，与页面布局一致
2. 图表区域加载时使用 `v-loading` 指令
3. 增加全局 `loading` 状态管理

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `frontend/src/views/quant/Dashboard.vue` | 增加骨架屏 |
| `frontend/src/views/quant/Strategy.vue` | 增加骨架屏 |
| `frontend/src/views/quant/ParamSweep.vue` | 增加骨架屏 |

### 3.8 前端错误边界 + 错误提示优化

**现状**：没有 `errorHandler`，后端错误透传，网络/业务错误无区分。

**改造**：
1. `App.vue` 增加全局 `errorCaptured` 和 `errorHandler`，组件崩溃时显示降级 UI
2. `api/index.js` 错误拦截器区分网络错误（`NetworkError`）和业务错误（`ApiError`）
3. 错误提示增加建议性操作引导

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `frontend/src/App.vue` | 增加 `errorCaptured` 和错误边界组件 |
| `frontend/src/api/index.js` | 错误分类 + 用户友好提示 |
| `frontend/src/components/ErrorBoundary.vue` | 新建错误边界组件 |

### 3.9 大数据量表格性能

**现状**：[FactorLibrary.vue](file:///home/joakim/Project/QuantLab/frontend/src/views/quant/FactorLibrary.vue) 使用 `el-table` 渲染全部行，因子 > 1000 时卡顿。

**改造**：
1. `el-table` 启用 `virtual-scroll` 虚拟滚动
2. 前端筛选改为后端分页查询
3. 排序列由前端计算改为后端排序

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `frontend/src/views/quant/FactorLibrary.vue` | 启用虚拟滚动，后端分页 |
| `backend/app/services/factor/library.py` | `list_factors()` 已支持分页，确认前端传参 |

---

## 四、P2 级优化（5-7 天）

### 4.1 Docker 部署加固

**现状**：[docker-compose.yml](file:///home/joakim/Project/QuantLab/docker-compose.yml) 缺少 CPU 限制、`start_period`、日志轮转。

**改造**：
```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 4g
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s   # 新增：qlib 初始化期间不判断为不健康
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"
```

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `docker-compose.yml` | 增加 CPU 限制、`start_period`、日志轮转 |
| `backend/Dockerfile` | 增加健康检查依赖（curl） |

### 4.2 数据库归档策略

**现状**：`backtest_result`、`sync_history` 等表无 TTL 清理。

**改造**：
1. `backtest_result` 增加 `deleted_at` 软删除，90 天前的数据自动标记
2. `sync_history` 保留最近 365 天
3. 增加 `cleanup_task` 定时任务（每周日凌晨 3:00 执行）
4. `nav_curve` 大字段启用 PostgreSQL 压缩（`TOAST`）

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/scheduler.py` | 增加 `cleanup_task` 定时清理 |
| `backend/app/services/task/update_service.py` | 新增 `cleanup_old_data()` |
| `backend/app/models/backtest_result.py` | 增加 `deleted_at` 字段 |

### 4.3 数据库连接池监控

**现状**：有连接池配置但无监控。

**改造**：在 `metrics.py` 中增加 Prometheus gauge：
```python
db_pool_size = Gauge("db_pool_size", "DB connection pool total size")
db_pool_available = Gauge("db_pool_available", "DB connection pool available connections")
db_pool_overflow = Gauge("db_pool_overflow", "DB connection pool overflow connections")
```

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/metrics.py` | 增加连接池指标 |
| `backend/app/core/database.py` | 连接池状态定时上报 |

### 4.4 日志系统结构化

**现状**：使用标准 `logging`，纯文本格式，无日志轮转。

**改造**：
1. 统一 JSON 结构化日志格式：`{"timestamp", "level", "logger", "message", "request_id", "module", "duration_ms"}`
2. 日志轮转：`RotatingFileHandler`（最大 100MB，保留 5 份）
3. 日志级别动态调整：通过 API 端点 `/api/v1/admin/log-level` 实时调整

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/logging_config.py` | 新建，配置 JSON 格式 + 轮转 |
| `backend/app/main.py` | 启动时加载 `logging_config` |
| `backend/app/api/admin.py` | 新增日志级别调整 API |
| `backend/app/core/config.py` | `LoggingSettings` 增加 `json_format`, `max_bytes`, `backup_count` |

### 4.5 CI/CD 配置

**现状**：无 CI/CD 配置。

**改造**：新建 `.github/workflows/ci.yml`：
```yaml
- name: Lint
  run: |
    cd backend && flake8 app/
    cd frontend && npm run lint
  
- name: Backend Test
  run: cd backend && pytest tests/ -v
  
- name: Frontend Test
  run: cd frontend && npm run test:unit
  
- name: Build
  run: |
    docker compose build
```

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `.github/workflows/ci.yml` | 新建 |

---

## 五、P3 级优化（4-5 天）

### 5.1 Prometheus 指标补充

**现状**：[metrics.py](file:///home/joakim/Project/QuantLab/backend/app/core/metrics.py) 缺少因子评价、LLM 调用、缓存命中率等关键指标。

**改造**：
```python
# 因子评价
eval_duration = Histogram("factor_eval_duration_seconds", "因子评价耗时", buckets=[1, 5, 10, 30, 60, 120])
eval_total = Counter("factor_eval_total", "因子评价总数", ["status"])

# LLM 调用
llm_call_duration = Histogram("llm_call_duration_seconds", "LLM 调用耗时")
llm_call_total = Counter("llm_call_total", "LLM 调用总数", ["provider", "status"])

# 缓存
cache_hit_total = Counter("cache_hit_total", "缓存命中总数", ["cache_name"])
cache_miss_total = Counter("cache_miss_total", "缓存未命中总数", ["cache_name"])

# 数据同步
sync_duration = Histogram("data_sync_duration_seconds", "数据同步耗时", ["source"])
sync_total = Counter("data_sync_total", "数据同步次数", ["source", "status"])
```

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/metrics.py` | 增加上述指标 |
| `backend/app/services/quant/factor_eval.py` | 埋点耗时 + 状态 |
| `backend/app/services/mining/llm_factor.py` | 埋点 LLM 调用 + 缓存 |
| `backend/app/services/data/sync_runner.py` | 埋点同步耗时 |

### 5.2 执行器优化

**现状**：[executor.py](file:///home/joakim/Project/QuantLab/backend/app/core/executor.py) 中 `run_cpu()` 使用 `ProcessPoolExecutor`，但因子评价用线程池就够。

**改造**：在 `executor.py` 中新增 `run_mixed()` 方法，根据任务类型自动选择线程池/进程池：
```python
async def run_mixed(func, *args, is_cpu_bound=False, **kwargs):
    if is_cpu_bound:
        return await run_cpu(func, *args, **kwargs)
    return await run_io(func, *args, **kwargs)
```

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `backend/app/core/executor.py` | 新增 `run_mixed()` 方法 |
| `backend/app/services/quant/factor_eval.py` | 调用 `run_mixed(is_cpu_bound=False)` |

### 5.3 请求 Trace 追踪

**现状**：无 `request_id` 贯穿日志，排查问题困难。

**改造**：
1. 在 `api/index.js` 请求拦截器中生成 `X-Request-ID`
2. 在 FastAPI middleware 中提取 `X-Request-ID`，注入 `logging.LoggerAdapter`
3. 后台任务生成自己的 `task_id` 作为 trace ID

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `frontend/src/api/index.js` | 请求头增加 `X-Request-ID` |
| `backend/app/main.py` | 增加 `request_id` middleware |
| `backend/app/core/logging_config.py` | `LoggerAdapter` 注入 `request_id` |

### 5.4 前端状态管理统一

**现状**：部分页面使用 Pinia store，部分直接调用 API 管理本地状态。

**改造**：统一策略：
- 所有页面级数据（因子列表、策略列表、任务列表）一律走 Pinia store
- store 中缓存策略统一（写操作后失效 + 重新拉取）
- 挖掘页面完成后自动刷新因子库 store

**涉及文件**：
| 文件 | 操作 |
|------|------|
| `frontend/src/stores/mining.js` | 新建 mining store |
| `frontend/src/views/quant/Mining.vue` | 挖掘完成 → 刷新 factor store |
| `frontend/src/stores/factor.js` | 暴露 `refreshList()` 方法 |

---

## 六、优化方案汇总表

### 6.1 按优先级排序

| 优先级 | 编号 | 优化项 | 工作量 | 依赖 |
|--------|------|--------|--------|------|
| P0 | F-01 | 因子挖掘样本分割 + 统计检验 | 3 天 | 无 |
| P0 | F-02 | Label 修复（5 日收益） | 0.5 天 | F-01 |
| P0 | S-01 | 表达式沙箱加固 | 1 天 | 无 |
| P0 | S-02 | JWT 过期 + 默认凭据加固 | 1 天 | 无 |
| P1 | P-01 | 因子评价 Pipeline 优化 | 3 天 | F-01 |
| P1 | F-03 | 滚动 IC + 时序稳健性 | 1.5 天 | F-01 |
| P1 | F-04 | 因子多样性约束 | 1 天 | F-01 |
| P1 | P-02 | IC 缓存上限保护 | 0.5 天 | 无 |
| P1 | P-03 | 数据同步增量合并 | 2 天 | 无 |
| P1 | UX-01 | WebSocket 推送 + 前端进度 | 3 天 | 无 |
| P1 | UX-02 | 前端骨架屏 + 加载状态 | 1 天 | 无 |
| P1 | UX-03 | 错误边界 + 错误提示优化 | 1 天 | 无 |
| P1 | UX-04 | 大数据量表格性能 | 1 天 | 无 |
| P2 | OPS-01 | Docker 部署加固 | 1 天 | 无 |
| P2 | DB-01 | 数据库归档策略 | 2 天 | 无 |
| P2 | DB-02 | 数据库连接池监控 | 1 天 | 无 |
| P2 | OPS-02 | 日志系统结构化 | 1 天 | 无 |
| P2 | OPS-03 | CI/CD 配置 | 1 天 | 无 |
| P3 | OPS-04 | Prometheus 指标补充 | 1 天 | 无 |
| P3 | P-04 | 执行器优化 | 1 天 | 无 |
| P3 | OPS-05 | 请求 Trace 追踪 | 1 天 | 无 |
| P3 | UX-05 | 前端状态管理统一 | 1 天 | 无 |

### 6.2 按领域分组

| 领域 | 优化项 | 总工作量 |
|------|--------|----------|
| **因子挖掘方法论** | F-01, F-02, F-03, F-04 | 6 天 |
| **性能优化** | P-01, P-02, P-03, P-04 | 6.5 天 |
| **安全加固** | S-01, S-02 | 2 天 |
| **用户体验** | UX-01, UX-02, UX-03, UX-04, UX-05 | 7 天 |
| **数据库** | DB-01, DB-02 | 3 天 |
| **运维** | OPS-01, OPS-02, OPS-03, OPS-04, OPS-05 | 5 天 |

---

## 七、预计收益

### 7.1 量化指标

| 指标 | 当前 | 优化后（预期） |
|------|------|---------------|
| 因子挖掘有效通过率 | 0%（100+ 个无效） | 10-20% 有效 |
| 因子评价吞吐量 | 约 4 个/分钟 | 约 20 个/分钟 |
| 页面加载时间 | 2-3 秒 | < 1 秒 |
| 任务状态更新延迟 | 5 秒（轮询） | < 0.5 秒（WebSocket） |
| 数据库表膨胀 | 无限制 | 90 天自动清理 |
| 生产环境安全等级 | 低（默认凭据） | 高（强制校验） |

### 7.2 体验指标

| 场景 | 当前体验 | 优化后体验 |
|------|----------|------------|
| 打开 Dashboard | 白屏加载 | 骨架屏 + 渐进加载 |
| 提交挖掘任务 | 无反馈 | 实时进度条 + 阶段显示 |
| 因子库加载 | 可能卡顿 | 虚拟滚动 + 后端分页 |
| 系统崩溃 | 白屏 | 错误边界组件 + 恢复引导 |
| 问题排查 | 无关联 | request_id 贯穿全链路 |

---

## 八、实施建议

### 8.1 执行顺序建议

1. **第一优先级**：P0 全部（F-01, F-02, S-01, S-02）
   - 因子挖掘问题是核心痛点，必须先解决
   - 安全问题是底线，不能拖

2. **第二优先级**：P1 中与因子挖掘相关的（F-03, F-04, P-01, P-02）
   - 完善因子评价体系，提升筛选质量
   - 优化评价性能，补充缓存保护

3. **第三优先级**：P1 中用户体验相关（UX-01, UX-02, UX-03, UX-04）
   - 提升使用体验，减少用户等待感

4. **第四优先级**：P2 运维和数据库（OPS-01, DB-01, DB-02, OPS-02, OPS-03）
   - 保障系统长期稳定运行

5. **第五优先级**：P3 完善性优化（OPS-04, P-04, OPS-05, UX-05）
   - 锦上添花的优化项

### 8.2 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 样本分割后因子通过率过低 | 用户可能无因子可用 | 提供可配置的显著性水平，设宽松/严格两档 |
| 执行器优化引入新 bug | 生产环境不稳定 | 先灰度发布，回滚方案 |
| 前端重构范围过大 | 开发周期长 | 逐步替换，每次只改一个页面 |
| 数据库归档误删数据 | 数据丢失 | 先软删除，30 天观察期后再物理删除 |

---

## 九、附录

### 9.1 相关文档

- [QuantLab_项目审视分析报告.md](file:///home/joakim/Project/QuantLab/docs/QuantLab_项目审视分析报告.md) — 问题发现
- [QuantLab_因子挖掘优化方案.md](file:///home/joakim/Project/QuantLab/docs/QuantLab_因子挖掘优化方案.md) — 因子挖掘专项优化

### 9.2 版本号建议

| 阶段 | 版本号 | 内容 |
|------|--------|------|
| P0 完成 | v2.5.0 | 因子质量 + 安全加固 |
| P1 完成 | v2.6.0 | 性能 + 体验优化 |
| P2 完成 | v2.7.0 | 运维 + 数据库加固 |
| P3 完成 | v2.8.0 | 可观测性 + 代码优化 |