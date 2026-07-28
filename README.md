# QuantLab - 量化策略回测研究平台

基于 FastAPI + Vue3 + qlib 的量化策略回测研究平台，支持因子评价、策略回测、AI 自动挖掘因子。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + SQLAlchemy + SQLite |
| 量化引擎 | pyqlib（微软AI量化框架） |
| 因子挖掘 | gplearn（符号回归）+ LLM（智谱GLM/硅基流动） |
| 数据源 | AKShare + chenditc/investment_data（qlib bin） |
| 前端 | Vue 3 + Element Plus + ECharts |

## 快速开始

```bash
# 1. 安装依赖（需 Python 3.11）
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. 配置环境变量
cp .env.example .env  # 填入 GLM_API_KEY（AI因子挖掘用，可选）

# 3. 启动
./start.sh dev
# 后端 http://localhost:8000  前端 http://localhost:3000
```

## 项目结构

```
backend/app/
├── services/
│   ├── quant/          # qlib 封装：数据适配、因子评价、回测引擎、组合指标
│   ├── factor/         # 因子库：表达式沙箱、CRUD、内置因子
│   ├── strategy/       # 策略管理 + 回测编排
│   ├── mining/         # AI 因子挖掘（LLM/符号回归/文本/AutoML）
│   ├── ai/             # LLM 客户端
│   └── data/           # AKShare 客户端
├── api/                # 路由：quant_data/factor/strategy/mining
├── models/             # ORM：Factor/Strategy/BacktestResult/MiningTask
└── core/               # 配置/数据库/异常/日志/调度

frontend/src/
├── views/quant/        # Dashboard/FactorLibrary/Strategy/Mining/DataStatus
└── api/                # quant/factor/strategy/mining
```

## 核心功能

- **因子库**：12 个内置因子（动量/反转/波动/换手等），表达式安全沙箱（防 look-ahead bias）
- **因子评价**：IC/RankIC/ICIR/换手率/衰减曲线
- **策略回测**：top-k dropout 选股，涨跌停/停牌过滤，日/周/月调仓，夏普/索提诺/回撤/卡玛
- **AI 因子挖掘**：① LLM 生成因子 ② 符号回归 ③ 文本因子 ④ AutoML 组合
- **数据管理**：qlib bin 数据源，定时增量同步

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/quant/data/qlib-status` | qlib 可用性 |
| POST | `/api/v1/quant/data/sync` | 同步股票数据 |
| GET | `/api/v1/factors` | 因子列表 |
| POST | `/api/v1/factors/seed-builtin` | 种子内置因子 |
| POST | `/api/v1/factors/{id}/evaluate` | 因子评价 |
| GET/POST | `/api/v1/strategies` | 策略 CRUD |
| POST | `/api/v1/strategies/{id}/backtest` | 策略回测 |
| POST | `/api/v1/mining/llm` | LLM 因子挖掘 |
| POST | `/api/v1/mining/symbolic` | 符号回归挖掘 |
| POST | `/api/v1/mining/automl` | AutoML 组合 |
| POST | `/api/v1/mining/text` | 文本因子挖掘 |
