# QuantLab - 量化策略回测研究平台

基于 **FastAPI + Vue 3 + qlib** 的量化策略回测研究平台，支持因子评价、策略回测、AI 自动挖掘因子。

![Python](https://img.shields.io/badge/Python-3.12-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-async-green) ![Vue](https://img.shields.io/badge/Vue-3-brightgreen) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue) ![License](https://img.shields.io/badge/license-MIT-blue)

> 当前版本：**v3.0.2** · 最后更新：2026-08-02

---

## 这是一份什么文档

本文档是 QuantLab 的**入门指南**。如果你刚接触这个项目，建议按下面顺序阅读：

1. 先读本文档的"快速开始"，把项目跑起来
2. 再读 [docs/TECHNICAL.md](docs/TECHNICAL.md) 了解整体架构
3. 根据你的角色，深入阅读对应的专题文档：
   - **想了解因子怎么挖的** → [docs/FACTOR_ENGINE.md](docs/FACTOR_ENGINE.md)
   - **想了解数据从哪里来** → [docs/DATA_LAYER.md](docs/DATA_LAYER.md)
   - **想调用后端 API** → [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
   - **想参与开发** → [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
   - **想知道最近的优化** → [docs/QuantLab_完整优化方案.md](docs/QuantLab_完整优化方案.md)

> 所有 docs/ 下的 Markdown 文档都可以在 Web 端 `/docs` 页面查看，右侧带**可点击的浮动目录**，点哪跳哪。目录会自动忽略代码块里的 `#` 注释，不会把 shell 说明误识别成章节。

---

## 一、技术栈总览

| 层 | 用到的技术 | 解决什么问题 |
|---|---|---|
| **后端框架** | FastAPI（asyncio）+ SQLAlchemy 2.0 + Pydantic | 高性能异步 API、自动 OpenAPI 文档 |
| **数据库** | PostgreSQL 16（asyncpg 驱动）+ Alembic 迁移 | 存因子、策略、回测结果、任务 |
| **量化引擎** | pyqlib（微软开源 AI 量化框架） | 因子计算、回测引擎、数据适配 |
| **因子挖掘** | gplearn（符号回归）+ LLM（多 Provider 自动 fallback） | 自动生成新因子 |
| **AI 因子验证** | 样本分割（60/20/20）+ 滚动 IC + t-test + 多样性检测 | 筛选真正有效的因子，防止过拟合 |
| **数据源** | baostock（主）+ akshare（新闻/市值/行业等补充） | A 股日频 OHLCV + 涨跌停 + 财务 + 新闻 |
| **前端** | Vue 3 + Element Plus + ECharts + Pinia | 交互式可视化分析 |
| **任务执行** | IO 线程池 + CPU 进程池 + `run_mixed` 自动选择器 | 不让 CPU 密集任务拖垮事件循环 |
| **GPU 加速** | 自动检测（torch / nvidia-smi） | LightGBM 训练、符号回归 |
| **可观测性** | Prometheus 指标 + JSON 结构化日志 + request_id 链路追踪 | 监控、排查、审计 |
| **安全** | JWT（exp + refresh）+ bcrypt + 生产环境安全闸门 + 表达式沙箱 | 防默认凭据、防任意代码执行 |
| **运维** | systemd/Nginx + 日志轮转 + CI/CD（GitHub Actions） | 部署、回归、监控 |

---

## 二、快速开始

### 2.1 本地开发（推荐先用这个跑通）

```bash
git clone https://github.com/JoakimStarr/QuantLab.git
cd QuantLab

# 一键引导：创建 .venv + 安装 Python/前端依赖 + 建数据目录
./setup.sh

# 配置环境变量
cp .env.example .env   # setup.sh 已自动复制，按需修改
# 至少需要配置一个 AI Provider（推荐 OPENCODEZEN_API_KEY）
# 还需配置 PostgreSQL 连接（POSTGRES_PASSWORD 等），详见 docs/QUICKSTART.md

# 启动
./start.sh
# 后端：  http://localhost:8000
# 前端：  http://localhost:3000
# 文档：  http://localhost:8000/docs  (Swagger UI)
# 指标：  http://localhost:8000/metrics (Prometheus)
```

### 2.2 生产部署

生产环境建议用 systemd 托管 + Nginx 反向代理，详见 [docs/DEPLOY.md](docs/DEPLOY.md)。

**生产部署前必须设置**：
```bash
APP_ENV=production
AUTH_ENABLED=true
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_PASSWORD=<足够强>
```

否则启动时会被安全闸门拦截。

### 2.3 第一次跑通要做什么

1. **同步数据**：打开 `DataStatus` 页面 → 点 `同步 baostock` → 等待 5~10 分钟（首次拉全市场历史）
2. **导入 Alpha158**：打开 `FactorLibrary` → 点 `导入 Alpha158` → 自动评价 158 个标准因子
3. **跑策略**：打开 `Strategy` → 选因子 → 点 `回测` → 看净值曲线

---

## 三、项目结构（一张图看懂）

```
QuantLab/
├── backend/                  # 后端 (Python 3.11 + FastAPI)
│   ├── app/
│   │   ├── services/
│   │   │   ├── quant/        # qlib 封装：因子评价、回测、组合指标
│   │   │   ├── factor/       # 因子库：表达式沙箱、CRUD、Alpha158、中性化、正交化
│   │   │   ├── strategy/     # 策略管理 + 回测编排 + 参数寻优 + walk-forward
│   │   │   ├── mining/       # AI 因子挖掘：LLM / 符号回归 / 文本 / AutoML
│   │   │   ├── ai/           # LLM 客户端 + 多 provider 路由（故障转移）
│   │   │   ├── data/         # AKShare / baostock + qlib bin 同步
│   │   │   └── docs/         # 技术文档加载器（喂给前端 /docs 页面）
│   │   ├── api/              # 路由：auth / quant / factor / strategy / mining / market
│   │   ├── models/           # ORM：Factor / Strategy / BacktestResult / MiningTask
│   │   ├── core/             # 配置 / 数据库 / 鉴权 / 限流 / 调度 / 执行器 / 任务恢复
│   │   ├── scheduler/        # APScheduler 任务（同步、归档、清理）
│   │   └── migrations/       # Alembic 迁移
│   ├── tests/                # pytest 测试套件
│
├── frontend/                 # 前端 (Vue 3 + Element Plus + ECharts)
│   ├── src/
│   │   ├── views/
│   │   │   ├── quant/        # Dashboard / FactorLibrary / Strategy / Mining 等业务页
│   │   │   ├── auth/         # Login
│   │   │   └── Docs.vue      # 技术文档展示页（带可点击目录）
│   │   ├── stores/           # Pinia: auth / app / factor / strategy / mining
│   │   ├── composables/      # useChartTheme / usePolling
│   │   ├── api/              # 后端调用封装
│   │   └── router/           # Vue Router
│   └── package.json
│
├── docs/                     # 技术文档（前端 /docs 页面读取这里）
├── config.yaml               # 全局配置（数据库/AI Provider/任务/挖掘等）
└── README.md                 # ← 你正在读
```

---

## 四、核心功能详解

### 4.1 因子库

**能做什么**：浏览、评价、对比、衰减分析各种因子。

**因子来源**：
- **内置因子**（12 个基础因子 + Alpha158 的 158 个标准因子）
- **LLM 挖掘**（让 AI 写 qlib 表达式）
- **符号回归**（gplearn 演化）
- **AutoML 组合**（LightGBM 把多个因子组合成"超级因子"）

**关键设计**：
- **表达式沙箱**：所有写入库的因子表达式都过 AST 白名单校验，禁止 `exec`/`eval`/`import` 等危险语法，禁止负数 `Ref`（防止 look-ahead bias）
- **多维验证**（v2.4.0+）：新因子不再只算 IC，而是按 60/20/20 切样本 + 滚动 IC + t-test 显著性 + 多样性去重
- **GPU 加速**（v2.4.0+）：符号回归、LightGBM 自动检测 GPU，有则用之

### 4.2 策略回测

**能做什么**：选一组因子 → 选调仓周期 → 看历史净值、夏普、回撤。

**核心特性**：
- **Top-K Dropout 选股**：每期选 IC 最高的 N 只，等权或 IC 加权
- **涨跌停 / 停牌过滤**：避免"涨停买不进"、"跌停卖不出"的回测偏差
- **调仓频率**：日 / 周 / 月
- **风险指标**：年化、夏普、索提诺、最大回撤、卡玛、胜率
- **滑点模型**：可配置基点（bps），模拟真实交易摩擦
- **Walk-Forward 验证**：滚动窗口验证策略稳定性

### 4.3 AI 因子挖掘

**四种挖掘方式**：

| 方式 | 适合场景 | 速度 | 因子质量 |
|------|----------|------|----------|
| **LLM** | 想让 AI 帮你想新因子 | 慢（依赖 LLM API） | 高（多轮迭代） |
| **符号回归** | 数据集小、特征明确的场景 | 中（GPU 加速） | 中 |
| **AutoML** | 把已有因子组合成"超级因子" | 慢（LightGBM 训练） | 高 |
| **文本因子** | 从新闻/公告里提取情绪信号 | 慢（依赖 LLM） | 视场景 |

**LLM 挖掘的关键步骤**（v2.4.0 改造后）：
1. 发送 prompt 给 AI，让它写 qlib 表达式
2. **样本分割**：60% 训练 + 20% 验证 + 20% 测试
3. **多维验证**：在验证集上计算滚动 IC，要求 IC > 0.03 + 稳定性 > 0.5 + 正占比 > 55% + 衰减 > -0.01
4. **显著性检验**：t-test，确保不是巧合
5. **多样性约束**：与已有因子 IC 相关性 < 0.8 才保留
6. **实时写入**：每算完一个因子立即 commit，前端 WebSocket 实时看到进度

### 4.4 数据管理

**数据源**：
- **baostock**（主源，一次拉全市场日K，含 ST 标记和估值字段）
- **akshare**（补充，新闻/市值/行业/EOD 增量兜底）

**同步策略**（v2.5.0+）：
- baostock 全量回填（`POST /quant/data/sync?years=N`，手动触发，从最新向旧逐交易日拉取）
- 幂等写入 PG（`ON CONFLICT DO NOTHING`），重复执行只补缺口
- 同步进度可通过接口查询

### 4.5 安全

**五道防线**：
1. **JWT 过期**：默认 24h（可配），refresh token 7 天
2. **密码强度检测**：注册/改密时强制要求字母+数字+特殊字符
3. **表达式沙箱**：禁止任何 Python 代码注入
4. **生产安全闸门**：`APP_ENV=production` + 默认密码/密钥 → 启动拒绝
5. **令牌桶限速**：登录 5/min，LLM 挖掘 3/min

### 4.6 可观测性

**四件套**：
- **JSON 结构化日志**：统一 `quantlab.log`（web）/ `error.log`（WARNING+，15 天保留）/ `sync.log`（同步 worker），每个请求带 `request_id`，同步任务带 `worker_kind`
- **Prometheus 指标**：`/metrics` 端点暴露，监控因子评价耗时、LLM 调用、缓存命中率、DB 连接池
- **结构化审计日志**：关键操作（登录/登出/挖掘/回测提交）随主日志输出（`logger=audit`，前端可按 logger 过滤）
- **健康检查**：`/api/v1/health` 检查 DB / 调度器 / 磁盘空间

---

## 五、API 一览（高频接口）

| 方法 | 路径 | 用途 | 限速 |
|---|---|---|---|
| POST | `/api/v1/auth/login` | 登录 | 5/min |
| GET | `/api/v1/quant/data/qlib-status` | qlib 是否可用 | - |
| POST | `/api/v1/quant/data/sync` | 触发数据同步 | - |
| GET | `/api/v1/factors` | 列出因子 | - |
| POST | `/api/v1/factors/{id}/evaluate` | 评价单个因子 | - |
| POST | `/api/v1/factors/compare` | 对比多个因子 | - |
| POST | `/api/v1/factors/seed-alpha158` | 导入 158 个标准因子 | - |
| POST | `/api/v1/factors/backfill-alpha158-metrics` | 补算历史因子指标 | - |
| POST | `/api/v1/strategies/{id}/backtest` | 跑回测 | - |
| POST | `/api/v1/strategies/{id}/param-sweep` | 参数寻优 | - |
| POST | `/api/v1/strategies/{id}/walk-forward` | 滚动验证 | - |
| POST | `/api/v1/mining/llm` | LLM 挖掘因子 | 3/min |
| POST | `/api/v1/mining/symbolic` | 符号回归挖掘 | - |
| POST | `/api/v1/mining/automl` | AutoML 因子组合 | - |
| WS | `/ws?token=<jwt>` | 实时任务进度 | - |

完整接口见 [docs/API_REFERENCE.md](docs/API_REFERENCE.md)。

---

## 六、关键配置

核心配置在 `config.yaml`，环境变量优先级更高。

| 配置项 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| 鉴权开关 | `AUTH_ENABLED` | 自动 | 跟随 `APP_ENV`，生产自动开启 |
| JWT 密钥 | `SECRET_KEY` | ⚠️ 默认值 | 生产必须改 |
| 管理员口令 | `ADMIN_PASSWORD` | ⚠️ admin/admin | 生产必须改 |
| 运行环境 | `APP_ENV` | development | production 触发安全闸门 |
| AI Provider | `OPENCODEZEN_API_KEY` / `GLM_API_KEY` / `SILICONFLOW_API_KEY` | - | 至少配一个，自动 fallback |
| 预测周期 | `config.mining.llm.eval_horizon` | 5 | 标签前向收益天数 |
| IC 阈值 | `config.mining.llm.ic_threshold` | 0.03 | 因子筛选阈值 |
| 显著性水平 | `config.mining.llm.significance_alpha` | 0.05 | t-test 阈值 |
| CPU 进程池 | `config.task.cpu_workers` | cpu_count/2 | 因子评价用 |
| IO 线程池 | `config.task.io_workers` | 8 | qlib 调用 |
| 令牌桶速率 | `config.quant.fetch_interval_seconds` | 1.2s | 数据同步间隔 |
| 滑点 | `config.quant.slippage_bps` | 0 | 回测滑点（基点） |

详见 [docs/TECHNICAL.md#配置](docs/TECHNICAL.md#六关键配置)。

---

## 七、测试

```bash
# 后端单元测试（pytest）
cd backend && python -m pytest tests/ -v

# 后端代码风格（ruff）
cd backend && ruff check app/

# 前端构建检查
cd frontend && npm run build

# 前端类型检查
cd frontend && npx vue-tsc --noEmit
```

CI 在每次 push 自动跑这些检查。

---

## 八、版本历史

| 版本 | 日期 | 主要变化 |
|---|---|---|
| **v3.0.2** | 2026-08-01 | 启动脚本修复；Docsify 替换为 markdown-it |
| **v3.0.1** | 2026-08-01 | empyrical→empyrical-reloaded 兼容 Python 3.12+ |
| **v3.0.0** | 2026-07-31 | 全面开源替代：用成熟框架替换自研实现 |
| **v2.5.5** | 2026-08-01 | 因子库表格列宽压缩 + 描述列展示 |
| **v2.5.4** | 2026-08-01 | 因子库新增"描述"列展示 |
| **v2.5.3** | 2026-08-01 | Alpha158 评价修复：用 Queue 解耦评价与 DB 写入 |
| **v2.5.2** | 2026-08-01 | Alpha158 实时写入：每因子立即 commit |
| **v2.5.1** | 2026-08-01 | Alpha158 批量评价加速（lru_cache + 预加载 + 线程池） |
| **v2.5.0** | 2026-08-01 | 完整优化方案：安全/性能/UX/运维/监控（21 项） |
| **v2.4.x** | 2026-08-01 | 因子挖掘多维验证 + GPU 检测 + 增量同步 + 性能优化 |
| **v2.3.x** | 2026-07-31 | 数据库迁移 PostgreSQL + 前端拆分 + 实时监控 |
| **v2.2.x** | 2026-07-30 | 因子评价引擎 + Alpha158 集成 |
| **v2.0.0** | 2026-07-15 | 初始版本：基础因子库 + 策略回测 + LLM 挖掘 |

---

## 九、文档导航

| 文档 | 内容 |
|---|---|
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | **架构总览**：分层、模块、执行模型、运维要点 |
| [docs/FACTOR_ENGINE.md](docs/FACTOR_ENGINE.md) | **因子引擎**：qlib 表达式语法、沙箱、评价指标、挖掘、回测 |
| [docs/DATA_LAYER.md](docs/DATA_LAYER.md) | **数据层**：涨跌停 mask、基本面 PIT、资金情绪采集、增量同步 |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | **API 参考**：所有后端接口 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | **开发手册**：环境、构建、代码规范、调试 |
| [docs/QuantLab_完整优化方案.md](docs/QuantLab_完整优化方案.md) | **优化路线图**：21 项优化点 + 实施时间表 |
| [docs/QuantLab_因子挖掘优化方案.md](docs/QuantLab_因子挖掘优化方案.md) | **因子挖掘专项**：样本分割、滚动 IC、显著性、多样性 |

> 所有文档在 Web 端 `/docs` 页面打开，**右侧自动生成可点击目录**，点哪跳哪。

---

## 十、常见问题

**Q：启动报 "qlib 未安装"？**
A：你用 Python 3.13 了。pyqlib 不支持 3.13，必须用 3.11。

**Q：登录提示 AI Provider 不可用？**
A：检查 `.env` 里至少配了一个 `*_API_KEY`。可以用 `curl http://localhost:8000/api/v1/auth/ai-status` 看哪个 Provider 在线。

**Q：Alpha158 导入后指标都是 NULL？**
A：跑一次 `/api/v1/factors/backfill-alpha158-metrics` 补算即可（v2.5.1+ 已支持实时写入，但历史数据需补算）。

**Q：回测很慢？**
A：检查 `config.task.cpu_workers` 是否够大；或启用 GPU 加速（v2.4.0+）。

**Q：数据同步卡住？**
A：检查 `baostock` 服务是否可达；日志看 `data/qlib_bin/sync.log`。

更多问题见 [docs/DEVELOPMENT.md#常见问题](docs/DEVELOPMENT.md)。

---

## License

MIT