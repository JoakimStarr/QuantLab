# QuantLab - 量化策略回测研究平台

基于 FastAPI + Vue3 + qlib 的量化策略回测研究平台，支持因子评价、策略回测、AI 自动挖掘因子。

![Python](https://img.shields.io/badge/Python-3.12-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-async-green) ![Vue](https://img.shields.io/badge/Vue-3-brightgreen) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue) ![License](https://img.shields.io/badge/license-MIT-blue)

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI (asyncio) + SQLAlchemy 2.0 + PostgreSQL (asyncpg) |
| 量化引擎 | pyqlib（微软AI量化框架） |
| 因子挖掘 | gplearn（符号回归）+ LLM（opencodezen/智谱GLM/硅基流动，多 provider 顺序 fallback） |
| 因子验证 | 样本分割(60/20/20) + 滚动IC + 统计显著性t-test + 多样性检测 |
| 数据源 | AKShare + chenditc/investment_data（qlib bin） |
| 前端 | Vue 3 + Element Plus + ECharts + Pinia |
| 任务执行 | IO 线程池 + CPU 进程池 + 自动选择器(run_mixed) |
| 可观测性 | Prometheus 指标 + JSON 结构化日志 + request_id 全链路追踪 |
| 安全 | JWT (exp/refresh) + bcrypt 密码 + 生产环境安全闸门 |

## 快速开始

```bash
# 1. 安装依赖（需 Python 3.11）
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. 配置环境变量
cp .env.example .env  # 填入 OPENCODEZEN_API_KEY / GLM_API_KEY / SILICONFLOW_API_KEY（三选一，支持故障转移）

# 3. 启动
./start.sh dev
# 后端 http://localhost:8000  前端 http://localhost:3000
```

### Docker 部署

```bash
docker compose up -d --build
```

> 生产部署前必须设置 `APP_ENV=production`、`AUTH_ENABLED=true`、`SECRET_KEY`、`ADMIN_PASSWORD`，否则安全闸门拒绝启动。详见 [技术文档](docs/TECHNICAL.md)。

## 项目结构

```
backend/app/
├── services/
│   ├── quant/          # qlib 封装：数据适配、因子评价、回测引擎、组合指标
│   ├── factor/         # 因子库：表达式沙箱、CRUD、Alpha158、中性化、正交化
│   ├── strategy/       # 策略管理 + 回测编排 + 参数寻优 + walk-forward
│   ├── mining/         # AI 因子挖掘（LLM/符号回归/文本/AutoML）
│   ├── ai/             # LLM 客户端 + 多 provider 路由（单例 + 顺序 fallback）
│   └── data/           # AKShare 客户端 + qlib bin 同步
├── api/                # 路由：auth/quant_data/factor/strategy/mining/market
├── models/             # ORM：Factor/Strategy/BacktestResult/MiningTask/TaskResult
├── core/               # 配置/数据库/鉴权/限流/调度/执行器/任务恢复
└── migrations/         # Alembic 迁移（增量列变更）

frontend/src/
├── views/quant/        # Dashboard/FactorLibrary/Strategy/Mining/DataStatus/BacktestCompare
├── views/auth/         # Login
├── stores/             # Pinia: auth/app/factor/strategy
├── composables/        # useChartTheme/usePolling
└── api/                # quant/factor/strategy/mining/auth + websocket
```

## 核心功能

- **因子库**：12 个内置因子 + Alpha158，表达式安全沙箱（AST 白名单 + 节点类型限制 + 复杂度上限，防 look-ahead bias），入库前 AST 规范化去重
- **因子评价**：多维验证（样本分割 60/20/20 + 滚动 IC + 统计显著性 t-test + 多样性检测），IC/RankIC/ICIR/换手率/衰减曲线
- **策略回测**：top-k dropout 选股，涨跌停/停牌过滤，日/周/月调仓，夏普/索提诺/回撤/卡玛，可选滑点
- **策略增强**：参数寻优（param-sweep）、walk-forward 滚动验证、回测对比
- **AI 因子挖掘**：① LLM 生成（迭代反馈，多维验证筛选）② 符号回归（GPU 加速并行）③ 文本因子 ④ AutoML 组合（LightGBM GPU 加速）
- **数据管理**：qlib bin 数据源，定时增量同步（智能检测最新交易日），令牌桶限速，行业同步
- **安全**：JWT 过期（可配置）+ refresh token（7天）+ 密码强度检测 + 生产环境安全闸门 + 表达式沙箱
- **可观测性**：JSON 结构化日志（RotatingFileHandler 100MB×5份）+ Prometheus 指标（因子评价/LLM/缓存/连接池）+ request_id 全链路追踪
- **运维韧性**：CPU 进程池隔离、任务持久化（崩溃重启自动重跑）、Alembic 迁移自动 upgrade、数据库归档清理（90天TTL）

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | 登录（5/min 限流） |
| GET | `/api/v1/auth/status` | 鉴权状态 |
| GET | `/api/v1/auth/ai-status` | 可用 AI Provider 探测 |
| GET | `/api/v1/quant/data/qlib-status` | qlib 可用性 |
| POST | `/api/v1/quant/data/sync` | 同步股票数据 |
| GET/POST | `/api/v1/factors` | 因子 CRUD |
| POST | `/api/v1/factors/seed-builtin` | 种子内置因子 |
| POST | `/api/v1/factors/{id}/evaluate` | 因子评价 |
| POST | `/api/v1/factors/compare` | 因子对比 |
| GET | `/api/v1/factors/{id}/decay` | IC 衰减曲线 |
| POST | `/api/v1/factors/seed-alpha158` | Alpha158 因子 |
| GET/POST | `/api/v1/strategies` | 策略 CRUD |
| POST | `/api/v1/strategies/{id}/backtest` | 策略回测 |
| POST | `/api/v1/strategies/{id}/param-sweep` | 参数寻优 |
| POST | `/api/v1/strategies/{id}/walk-forward` | walk-forward 验证 |
| POST | `/api/v1/mining/llm` | LLM 因子挖掘（3/min 限流） |
| POST | `/api/v1/mining/symbolic` | 符号回归挖掘 |
| POST | `/api/v1/mining/automl` | AutoML 组合 |
| POST | `/api/v1/mining/text` | 文本因子挖掘 |
| WS | `/ws?token=<jwt>` | 实时任务进度/日志推送 |

完整接口见 [技术文档](docs/TECHNICAL.md)。

## 配置

核心配置在 `config.yaml`，环境变量优先级更高。关键项：

| 配置 | 环境变量 | 说明 |
|---|---|---|
| 鉴权开关 | `AUTH_ENABLED` | 未设时按 APP_ENV 判定 |
| JWT 密钥 | `SECRET_KEY` | 生产必须改强随机串，默认 24h 过期 |
| 管理员口令 | `ADMIN_PASSWORD` / `ADMIN_PASSWORD_HASH` | bcrypt 哈希优先 |
| 运行环境 | `APP_ENV` | development/production，影响安全闸门与 docs |
| CORS 来源 | `CORS_ORIGINS` | 逗号分隔，分离部署时设置 |
| 预测周期 | `config.mining.llm.eval_horizon` | 标签前向收益天数，默认 5 日 |
| IC 阈值 | `config.mining.llm.ic_threshold` | 因子筛选 IC 阈值，默认 0.03 |
| 统计显著性 | `config.mining.llm.significance_alpha` | t-test 显著性水平，默认 0.05 |
| 多样性阈值 | `config.mining.llm.diversity_threshold` | 因子 IC 相关性去重阈值，默认 0.8 |
| CPU 进程池 | `config.task.cpu_workers` | 默认 cpu_count//2 |
| 日志级别 | `LOG_LEVEL` | 动态调整，支持 DEBUG/INFO/WARNING/ERROR |
| 令牌桶速率 | `config.quant.fetch_interval_seconds` | AKShare 请求间隔，默认 1.2s |
| 滑点 | `config.quant.slippage_bps` | 回测滑点（基点），0 关闭 |

## 测试

```bash
# 后端
cd backend && python -m pytest tests/ -v

# 前端构建检查
cd frontend && npm run build
```

## 版本历史

| 版本 | 日期 | 内容 |
|------|------|------|
| v2.5.1 | 2026-08-01 | Alpha158 批量评价加速：lru_cache 股票池 + 预加载 label/close + 线程池 + 批次写入 + WebSocket 进度 |
| v2.5.0 | 2026-08-01 | 完整优化方案：安全加固/性能优化/UX/运维/监控 |
| v2.4.0 | 2026-08-01 | 因子挖掘多维验证 + GPU 检测支持 |
| v2.3.2 | 2026-08-01 | 修复 init_db 建表缺失问题 |
| v2.4.2 | 2026-08-01 | Alpha158 导入自动评价 + 历史因子补算指标 |
| v2.4.1 | 2026-08-01 | Alembic 迁移补索引 / 回测结果 DB 持久化 |
| v2.4.0 | 2026-08-01 | 并行 IC 评价 + 令牌桶限速 + 连接池调优 + 参数扫描并行 |

CI 通过 ruff + pytest + 前端 build。

## 文档

- [技术文档](docs/TECHNICAL.md)：架构设计、模块说明、执行模型、运维要点

## License

MIT
