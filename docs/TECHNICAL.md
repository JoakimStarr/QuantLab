# QuantLab 技术文档 - 架构总览

> 版本：v3.1.0 · 最后更新：2026-08-06
> 配套仓库：[JoakimStarr/QuantLab](https://github.com/JoakimStarr/QuantLab)

本文档是 QuantLab 的**架构总览**：回答"整个系统由哪些模块组成、各模块怎么协作、数据从哪来到哪去"。读完后你应该能在脑海里画出系统的完整地图。

---

## 这是什么文档 / 怎么读

如果你刚接触 QuantLab，建议：
1. **5 分钟概览**：读完第 1、2 节即可
2. **想了解每个模块细节**：跳到对应章节
3. **专题深入**：参考 [README.md](../README.md) 里的文档导航

---

## 一、系统整体架构

### 1.1 一张图看懂

```
┌────────────────────────────────────────────────────────────────────┐
│  前端层  Vue 3 + Element Plus + ECharts + Pinia                    │
│  ├─ Dashboard / FactorLibrary / Strategy / Mining                  │
│  ├─ DataStatus / BacktestCompare / FactorCompare / FactorDeepAnalysis│
│  ├─ Docs.vue（带可点击目录的文档浏览器）                              │
│  └─ WebSocket 客户端（任务进度/日志实时推送）                          │
└────────────────────────┬───────────────────────────────────────────┘
                         │ HTTP REST + WebSocket
┌────────────────────────▼───────────────────────────────────────────┐
│  后端层  FastAPI (asyncio) + SQLAlchemy 2.0 + Pydantic             │
│  ├─ api/        路由层（auth/quant_data/factor/strategy/mining/market）│
│  ├─ core/       配置/数据库/鉴权/限流/调度/执行器/任务恢复/Metrics  │
│  ├─ models/     ORM（Factor/Strategy/BacktestResult/MiningTask/...）│
│  ├─ scheduler/  APScheduler 定时任务（因子衰减、任务回收、归档、清理）     │
│  ├─ migrations/ Alembic 数据库迁移                                   │
│  └─ services/   业务核心                                             │
│     ├─ quant/   qlib 封装（数据适配/因子评价/回测引擎/组合指标）       │
│     ├─ factor/  因子库（表达式沙箱/CRUD/Alpha158/中性化/正交化）      │
│     ├─ strategy/ 策略管理 + 回测编排 + 参数寻优 + walk-forward       │
│     ├─ mining/  AI 因子挖掘（LLM/符号回归/文本/AutoML）              │
│     ├─ ai/      LLM 客户端 + 多 provider 路由（故障转移）             │
│     ├─ data/    AKShare/baostock + qlib bin 同步（增量）             │
│     └─ docs/    技术文档加载器（喂给前端 /docs 页面）                 │
└────────────────────────┬───────────────────────────────────────────┘
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
   pyqlib 引擎       baostock/AKShare   LLM Providers
   (CPU 进程池       /qlib bin          (opencodezen
   + IO 线程池)      (IO 线程池)        /GLM/SiliconFlow)
```

### 1.2 进程模型

QuantLab 是一个**单进程多线程 + 多进程池**架构：

| 组件 | 类型 | 用途 | 默认大小 |
|------|------|------|----------|
| **Web 进程** | uvicorn 单进程 asyncio | HTTP + WebSocket | 1 |
| **IO 线程池** | `ThreadPoolExecutor` | qlib 数据读取、AKShare 同步 | `io_workers=8` |
| **CPU 进程池** | `ProcessPoolExecutor` | 纯函数 CPU 任务（因子评价、CV 训练） | `cpu_workers=4` |
| **后台任务** | `asyncio.create_task` | 挖掘/回测在事件循环内调度 | 按需创建 |

**为什么分 IO 线程池和 CPU 进程池？**

- **qlib C 扩展释放 GIL** → 线程池足够并发，进程池反而有 pickle 序列化开销
- **ProcessPoolExecutor 绕过 GIL** → 适合纯 Python 计算密集（gplearn 演化）
- **不分池的代价**：CPU 任务占满线程池 → 健康检查等同步请求排队 → 服务看起来挂了

### 1.3 数据流向

**典型场景：用户点击"挖掘 LLM 因子"按钮**

```
浏览器 (Mining.vue)
  │
  ├─ POST /api/v1/mining/llm  (HTTP)
  │
  ▼
FastAPI 路由 (api/mining_ext.py)
  │
  ├─ 限流检查 (3/min)
  ├─ 鉴权检查 (require_user)
  ├─ 创建 MiningTask 记录 (DB status=pending)
  ├─ asyncio.create_task(mine_with_llm_iterative(task_id))
  ├─ 立即返回 task_id（不阻塞）
  │
  ▼
事件循环后台任务 (services/mining/llm_factor.py)
  │
  ├─ ProviderRouter.call() ────────────► LLM API (opencodezen)
  │     ├─ prompt：让 AI 写 qlib 表达式
  │     └─ 返回 N 个候选表达式
  │
  ├─ evaluate_factor_with_validation()  ──── 走 IO 线程池 ────► qlib
  │     ├─ 加载预加载的 label/close
  │     ├─ 计算 IC/RankIC/ICIR/turnover/decay
  │     └─ 样本分割 + 滚动 IC + t-test + 多样性
  │
  ├─ _update_single_factor_metrics()  ──── 走单 DB writer 协程 ────► PostgreSQL
  │
  ├─ WebSocket broadcast("task_progress") ────► 浏览器
  │
  ▼
浏览器收到推送 → Mining.vue 进度条更新 → 用户看到结果
```

---

## 二、核心模块详解

### 2.1 配置（`backend/app/core/config.py`）

**作用**：从 `config.yaml` + 环境变量加载配置，启动时合并。

**三层优先级**（高 → 低）：
1. 环境变量（如 `OPENCODEZEN_API_KEY`）
2. `config.yaml`
3. 代码内默认值

**`Settings` 结构**：
```python
Settings
├─ app        # 名称/版本/调试/时区
├─ api        # CORS、限流
├─ auth       # JWT 过期、refresh token、密码强度
├─ security   # 生产安全闸门
├─ database   # 连接池大小
├─ quant      # 数据同步、回测区间、滑点
├─ mining     # LLM/符号回归/AutoML 各项配置
├─ task       # CPU/IO 池大小、超时
├─ logging    # JSON 格式、轮转
└─ ai         # LLM Provider 配置
```

**安全闸门**（`enforce_production_security()`）：
- 触发条件：`APP_ENV == "production"`
- 检查项：默认 `SECRET_KEY`、默认 `ADMIN_PASSWORD`、未开 `AUTH_ENABLED`
- 任一未通过 → 抛 `RuntimeError` 拒绝启动
- 同时关闭 `/docs`、`/redoc`、`/openapi.json`（生产不暴露 API 文档）

### 2.2 执行器（`backend/app/core/executor.py`）

| 函数 | 池 | 适用 | 注意事项 |
|------|-----|------|----------|
| `run_io(func, ...)` | ThreadPool | 同步 IO（AKShare/qlib） | 无 |
| `run_io_cpu(func, ...)` | ThreadPool | 释放 GIL 的 CPU 任务（qlib C 扩展） | **无 pickle 开销** |
| `run_cpu(func, ...)` | ProcessPool | 纯 Python CPU 密集（gplearn、CV 训练） | 函数必须是模块级，参数可 pickle |
| `run_mixed(func, ..., is_cpu_bound=False)` | 自动选择 | 一键调用 | True 走 CPU 池，False 走 IO 池 |

**典型调用**：
```python
from app.core.executor import run_io_cpu, run_cpu

# qlib 因子评价（IO + 释放 GIL 的 CPU）→ 线程池
metrics = await run_io_cpu(evaluate_factor, expr, start, end)

# 符号回归演化（纯 Python CPU）→ 进程池
est.fit(X, y)  # 在 run_cpu 内运行
```

### 2.3 鉴权（`backend/app/core/auth.py`）

**JWT 配置**：
- 算法：`HS256`
- 默认过期：24h（可配 `access_token_expire_hours`）
- refresh token：7 天（`refresh_token_expire_days`）
- 密钥：`SECRET_KEY`（生产必须改）

**传递方式**：
- `Authorization: Bearer <token>`（前端默认）
- Cookie `__Host-token`（更安全，需 HTTPS）

**依赖注入**：
- `require_user` — 必须登录
- `optional_user` — 可选登录（开发模式关鉴权时放行）

**密码强度**：注册/改密时检查，要求字母 + 数字 + 特殊字符组合。

**限流**（`backend/app/core/ratelimit.py`）：
- 登录：5 次/分钟
- LLM 挖掘：3 次/分钟
- 用 `slowapi` 实现

### 2.4 数据库（`backend/app/core/database.py`）

**仅支持 PostgreSQL 16**（asyncpg 驱动），无 SQLite 回退。连接串由 `DATABASE_URL` 或 `POSTGRES_USER/PASSWORD/DB/HOST/PORT` 环境变量解析（`app/core/config.py` 的 `model_post_init` 加载 `.env`）。

**连接池**：
```python
pool_size=10        # 默认 10 个长连接
max_overflow=20     # 最多额外 20 个溢出连接
pool_pre_ping=True  # 借出前 ping 检测死连接
pool_recycle=3600   # 1 小时回收
```

**自动初始化**：
```python
async def init_db():
    Base.metadata.create_all(engine)  # 建新表
    await run_alembic_upgrade()       # 子线程跑迁移
```

**迁移管理**：用 Alembic，每次 schema 变更写一个 revision，CI 跑 `alembic upgrade head` 校验。

**Prometheus 指标**（`db_pool_size`、`db_pool_available`、`db_pool_overflow`）— 监控连接池使用率。

### 2.5 任务恢复（`backend/app/core/recovery.py`）

应用启动时自动调用：

| 函数 | 作用 |
|------|------|
| `recover_stale_sync` | 标记卡死的同步任务为 failed |
| `recover_stale_mining` | 回收僵尸挖掘任务（running 超时） |
| `rerun_pending_mining` | 重启后重跑近 3 天的 pending/running 挖掘任务 |

**意义**：进程崩溃后任务不丢，自动恢复。

### 2.6 量化引擎（`backend/app/services/quant/`）

| 文件 | 职责 |
|------|------|
| `qlib_init.py` | qlib 单例初始化（线程锁保护，可并发调用） |
| `data_adapter.py` | qlib 数据读取封装 |
| `factor_eval.py` | 因子评价（IC/RankIC/ICIR/换手/衰减），AutoML 表达式经训练 bundle 解析 |
| `factor_validator.py` | **多维验证**：样本分割 + 滚动 IC + t-test + 多样性 |
| `factor_monitor.py` | 因子衰减检测（定时任务 18:05） |
| `backtest_engine.py` | top-k dropout 选股回测 + 涨跌停/停牌过滤 + 滑点 |
| `qlib_backtest.py` / `vbt_backtest.py` / `rule_backtest.py` | qlib / vbt / 规则策略回测后端 |
| `portfolio.py` / `portfolio_report.py` | 组合指标（年化、夏普、索提诺、最大回撤、卡玛） |
| `walk_forward.py` | walk-forward 滚动回测 |

**qlib 初始化的陷阱**：
- pyqlib 不支持 Python 3.13，必须 3.11
- 首次调用需要初始化（~2 秒），后续直接复用单例
- 多线程并发调用安全（内部用锁）

### 2.7 因子库（`backend/app/services/factor/`）

| 文件 | 职责 |
|------|------|
| `expression.py` | 表达式沙箱（AST 白名单 + look-ahead bias 检测） |
| `library.py` | 因子 CRUD + 批量导入 + 评价更新 |
| `alpha158.py` | 158 个 qlib 标准因子导入 + 批量评价/回补指标 |
| `neutralize.py` | 中性化（市值、行业） |
| `orthogonalize.py` | 正交化（去除与已有因子的相关性） |
| `factor_compare.py` | 因子对比 |
| `ai_explain.py` | LLM 因子解释 |
| `builtin_factors.py` / `etf_factors.py` | 内置因子 / ETF 因子 |

**表达式沙箱关键点**：
- 算子白名单（`Ref/Mean/Std/Max/Min/Sum/Rank/Corr/Cov/Delta/Slope/Resi/WMA/EMA` 等）
- 字段白名单（`$close/$open/$high/$low/$volume/$amount/$factor`）
- AST 检查禁止：`exec/eval/import/__/getattr` 等
- **禁止负数 Ref**（防止 look-ahead bias）

**Alpha158 批量评价（v2.5.1~v2.5.3 持续优化）**：
- v2.5.1：`lru_cache` 缓存股票池 + 预加载 label/close + 线程池 + 批次写入
- v2.5.2：每因子实时 commit（替代批次写入）
- v2.5.3：用 `asyncio.Queue` 解耦评价与 DB 写入（避免连接池枯竭）

### 2.8 AI 因子挖掘（`backend/app/services/mining/`）

四种挖掘方式，**均做多维验证**：

| 类型 | 入口文件 | 引擎 | 特点 |
|------|----------|------|------|
| **LLM** | `llm_factor.py` | 多 provider 路由 | 迭代反馈改进，可指定 n_rounds |
| **符号回归** | `symbolic.py` | gplearn 遗传编程 | 时序分割防泄露，Pareto 前沿取 top |
| **文本因子** | `text_factor.py` | 新闻情感 + LLM | 指定股票代码或 universe 前 30 |
| **AutoML** | `automl.py` | LightGBM/Ridge | 因子组合，时序 CV 评估（**GPU 自动加速**） |

**LLM Provider 路由**（`backend/app/services/ai/provider_router.py`）：
- 单例，全局复用 `AsyncOpenAI` 连接池
- 顺序 fallback：primary → fallback → tertiary
- 失败自动重试，下次调用重置 `_initialized`

### 2.9 数据层（`backend/app/services/data/`）

详见 [docs/DATA_LAYER.md](DATA_LAYER.md)。

要点：
- **baostock** 是主源（一次拉全市场日K，含 ST 标记和估值字段）+ ETF 日K
- **akshare** 作补充（宏观指标/财报摘要/指数/外盘/EOD 增量兜底）
- **一键全同步**：`POST /api/v1/quant/data/sync-full?years=N`，按序串联 A股回填 → 指数 → 宏观(广播) → 财报(拉取+广播) → 外盘（bin 需对齐最终日历）
- **ETF**：`POST /api/v1/quant/data/sync-etf`（`etf_daily` 窄表 + `instruments/etf_all.txt` + `stock_index(type='etf')`）
- **幂等写入**：PG 使用 `ON CONFLICT DO NOTHING`，重复执行只补缺口

### 2.10 GPU 检测（`backend/app/core/gpu_utils.py`）

```python
from app.core.gpu_utils import is_gpu_available, get_device

if is_gpu_available():
    device = get_device()  # 'cuda' or 'cpu'
    # LightGBM: params['device'] = 'gpu'
    # 符号回归: n_jobs=-1
```

检测顺序：torch.cuda → nvidia-smi → 兜底 False。

---

## 三、API 概览

所有 API 前缀 `/api/v1`，响应统一 `{ok, data, error}` 格式。

| 模块 | 代表端点 | 说明 |
|------|----------|------|
| `auth` | `POST /auth/login`、`GET /auth/status`、`GET /auth/ai-status` | 登录、状态、可用 provider 探测 |
| `quant_data` | `GET /quant/data/qlib-status`、`POST /quant/data/sync-full` | qlib 可用性、数据同步 |
| `data_ext` | `POST /quant/data/eod-sync`、`/sync-etf`、`/fundamental/sync`、`GET /quant/data/validate`、`POST /quant/data/repair` | 增量同步/校验/补齐 |
| `macro` | `POST /macro/sync`、`GET /macro/indicators`、`/macro/status`、`/macro/snapshot` | 宏观指标同步/查询 |
| `factor` | `GET /factors`、`POST /factors/{id}/evaluate` | 因子 CRUD + 评价 |
| `factor_ext` | `POST /factors/compare`、`GET /factors/{id}/decay`、`POST /factors/seed-alpha158` | 因子对比、衰减、Alpha158、中性化 |
| `strategy` | `POST /strategies/{id}/backtest`、`GET /strategies/{id}/backtest-results` | 策略 + 回测 |
| `strategy_ext` | `POST /strategies/{id}/param-sweep`、`POST /strategies/{id}/walk-forward` | 参数寻优、walk-forward |
| `mining` | `POST /mining/llm`、`/symbolic`、`/automl`、`/text` | AI 因子挖掘（3/min 限流） |
| `mining_ext` | `GET /mining/templates`、`POST /mining/templates/{key}/run` | 挖掘模板 |
| `market` | 行情数据 | K线、盘口 |

**WebSocket**：`/ws?token=<jwt>`，推送事件：
- `task_progress` — 任务进度（挖掘、回测、同步）
- `alpha158_progress` — Alpha158 因子逐个完成事件
- `sync_log` — 数据同步日志

完整接口见 [docs/API_REFERENCE.md](API_REFERENCE.md)。

---

## 四、前端架构

```
frontend/src/
├── views/
│   ├── Docs.vue             # 技术文档（带可点击目录）
│   ├── auth/Login.vue       # 登录
│   └── quant/
│       ├── Dashboard.vue            # 首页概览
│       ├── FactorLibrary.vue        # 因子库（CRUD + 评价）
│       ├── FactorCompare.vue        # 因子对比
│       ├── FactorDeepAnalysis.vue   # 深度分析（分布、衰减、显著性）
│       ├── Strategy.vue             # 策略回测
│       ├── StrategyLibrary.vue      # 策略库
│       ├── BacktestCompare.vue      # 回测对比
│       ├── Mining.vue               # AI 因子挖掘
│       ├── DataStatus.vue           # 数据管理（同步/校验/补齐/指数）
│       ├── Macro.vue                # 宏观指标
│       └── Logs.vue                 # 日志
├── stores/                  # Pinia 状态管理
│   ├── auth.js              # 用户登录态
│   ├── app.js               # 全局 UI 状态
│   ├── factor.js            # 因子列表缓存
│   ├── strategy.js          # 策略列表缓存
│   └── mining.js            # 挖掘任务 + WebSocket
├── composables/             # 组合式函数
│   ├── useChartTheme.js     # ECharts 主题切换
│   └── usePolling.js        # 轮询降级
├── api/                     # 后端 API 封装
├── router/                  # Vue Router 配置
├── stores/                  # Pinia
└── main.ts
```

**前端关键设计**：

| 特性 | 实现 |
|------|------|
| 路由 | Vue Router 4，hash 模式 |
| 状态管理 | Pinia（替代 Vuex） |
| UI 组件库 | Element Plus |
| 图表 | ECharts 5（支持暗色主题切换） |
| HTTP 客户端 | axios + 拦截器（自动带 JWT、统一错误处理） |
| WebSocket | 原生 WS + 心跳（断线自动重连） |
| Markdown 渲染 | markdown-it + highlight.js |
| 文档目录 | 从 h2/h3 自动提取 + IntersectionObserver 高亮 |

**文档页面（`Docs.vue`）核心特性**：
- 顶部文档切换器（按分组排序）
- 字号控制（A-/A+，localStorage 持久化）
- 目录位置切换（左/右）
- 自动提取 h2/h3 目录，并忽略 fenced code block 内的 `#` 注释
- 点击目录平滑滚动 + 锚点定位
- 滚动时 IntersectionObserver 高亮当前章节
- 全部 API 见 `/docs` 页面

---

## 五、API 文档

完整接口见 [docs/API_REFERENCE.md](API_REFERENCE.md)。包括：

- 每个端点的请求/响应格式
- 错误码表
- 鉴权要求
- 限流规则
- 完整 curl 示例

---

## 六、关键配置

核心配置在 `config.yaml`，环境变量优先级更高。完整列表见 [README.md#六关键配置](../README.md)。

---

## 七、测试与质量

### 7.1 后端测试

```bash
cd backend
python -m pytest tests/ -v          # 跑全部
python -m pytest tests/test_X.py    # 跑单个
ruff check app/                      # 代码风格
```

**当前覆盖**：auth、expression、factor_eval、backtest_engine、symbolic、websocket_manager、config_compat。

### 7.2 前端测试

```bash
cd frontend
npm run build                        # 类型检查 + 打包
npx vue-tsc --noEmit                 # 仅类型检查
```

### 7.3 CI

`.github/workflows/ci.yml`：
- Lint（ruff + 前端 ESLint）
- 后端 pytest
- 前端 build
- Postgres 服务集成测试

---

## 八、部署

### 8.1 开发环境

```bash
pip install -r requirements-dev.txt
cd frontend && npm install && cd ..
cp .env.example .env
./start.sh dev
```

### 8.2 生产部署

生产部署（systemd + Nginx）详见 [docs/DEPLOY.md](DEPLOY.md)。

### 8.3 CORS

默认 `http://localhost:3000`。分离部署时：
```bash
CORS_ORIGINS=https://app.your-domain.com,https://your-domain.com
```

---

## 九、运维要点

| 项目 | 建议 |
|------|------|
| **CPU 任务隔离** | `config.task.cpu_workers=cpu_count//2`，避免与 Web 争抢 |
| **任务持久化** | 进程崩溃后会自动重跑近 3 天的挖掘任务，无需手动干预 |
| **数据库迁移** | 新增字段时写 Alembic revision，启动自动 upgrade |
| **日志** | `logs/` 下 `quantlab.log`（web 全量，轮转 100MB×5）、`error.log`（web WARNING+，备份保留 15 天）、`sync.log`（同步 worker，行内 `worker_kind` 字段区分任务）。统一 structlog JSON 格式；`PUT /logs/level` 可运行时调级 |
| **数据库备份** | PostgreSQL 用 `pg_dump` |
| **监控** | Prometheus 抓 `/metrics`，Grafana 可视化 |
| **告警** | 关注 `factor_eval_duration_seconds`（评价耗时）、`db_pool_available`（连接池） |

---

## 十、关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| CPU 密集任务 | `ProcessPoolExecutor` | GIL 下线程无加速，且会拖垮事件循环 |
| 任务持久化 | DB 状态机 + 重启重跑 | 无需引入 Redis/Celery，契合单机研究平台规模 |
| 迁移策略 | create_all + Alembic ALTER | 低成本维护 schema |
| LLM 路由 | 顺序 fallback 单例 | 免费模型超时长，并发竞速浪费预算 |
| 表达式安全 | AST 白名单 | 防 look-ahead bias + 代码注入，比正则更严格 |
| 多维验证 | 样本分割 + 滚动 IC + t-test + 多样性 | 单一 IC 指标易过拟合 |
| 数据库 | PostgreSQL 16 | 强约束 + JSONB，asyncpg 驱动，无 SQLite 回退 |

---

如有疑问，提 [GitHub Issue](https://github.com/JoakimStarr/QuantLab/issues)。