# session_id: a34acae8-d96e-4a00-925e-85b3919fa735
classes: {
  zone_1: {
    style: {
      fill: transparent
      stroke: "#4185BF"
      font-color: "#333333"
      border-radius: 8
    }
  }
  zone_2: {
    style: {
      fill: transparent
      stroke: "#4185BF"
      font-color: "#333333"
      border-radius: 8
    }
  }
  zone_3: {
    style: {
      fill: transparent
      stroke: "#4185BF"
      font-color: "#333333"
      border-radius: 8
    }
  }
  zone_4: {
    style: {
      fill: transparent
      stroke: "#4185BF"
      font-color: "#333333"
      border-radius: 8
    }
  }
  zone_5: {
    style: {
      fill: transparent
      stroke: "#4185BF"
      font-color: "#333333"
      border-radius: 8
    }
  }
  entity: {
    style: {
      fill: "#FFFFFF"
      stroke: "#1F2937"
      font-color: "#333333"
      border-radius: 6
    }
  }
  signal: {
    style: {
      fill: transparent
      font-color: "#6B7280"
    }
  }
}

direction: down

# ============================================
# 标题
# ============================================
title: QuantLab 量化策略研究平台 — 系统架构图 {
  shape: text
  style.font-size: 28
  style.bold: true
}

# ============================================
# 前端层 (Vue 3)
# ============================================
frontend: 前端层 (Vue 3) {
  class: zone_1

  vue_spa: Vue 3 单页应用 {
    class: entity
  }

  pages: 功能页面 {
    class: entity
    grid-rows: 2
    grid-columns: 3

    dashboard: Dashboard
    factor_lib: 因子库
    backtest: 策略回测
    ai_mining: AI挖掘
    data_mgmt: 数据管理
    macro: 宏观指标
  }

  state: Pinia 状态管理 {
    class: entity
  }

  http: axios REST 调用 {
    class: entity
  }

  ws: WebSocket 接收推送 {
    class: entity
  }
}

# ============================================
# API层 (FastAPI)
# ============================================
api: API层 (FastAPI) {
  class: zone_2

  rest: REST API /api/v1 {
    class: entity
    grid-columns: 5

    factor: 因子
    strategy: 策略
    backtest: 回测
    mining: 挖掘
    sync: 数据同步
  }

  envelope: "响应信封 {ok,data,error}" {
    class: entity
  }

  core: 核心基础组件 {
    class: entity
    direction: down

    config: 配置模块
    db: 异步 SQLAlchemy
    jwt: JWT 鉴权
    io_exec: io_executor
    cpu_exec: cpu_executor
    scheduler: APScheduler
    ws_mgr: WebSocket 管理器
  }
}

# ============================================
# 服务层
# ============================================
service: 服务层 {
  class: zone_3

  business: 业务服务 {
    class: entity
    direction: down

    factor_eval: 因子评价 (IC/RankIC) {
      class: entity
    }

    backtest_engine: 回测引擎 {
      class: entity
      direction: down

      qlib: qlib 后端
      vbt: vbt 后端
    }

    ai_mining: AI 因子挖掘 {
      class: entity
      grid-columns: 2

      llm: LLM
      symbol: 符号回归
      automl: AutoML
      text: 文本
    }

    ai_enhance: AI 增强 {
      class: entity
      direction: down

      explain: 因子解释
      generate: 策略生成
    }
  }

  sync_workers: 同步 Worker (独立子进程) {
    class: entity

    baostock: baostock 爬虫
    akshare: akshare 爬虫
    eastmoney: 东财爬虫
    progress: data/sync_progress.json {
      class: signal
    }
  }
}

# ============================================
# 数据层 (双存储)
# ============================================
data: 数据层 (双存储) {
  class: zone_4

  pg: PostgreSQL 业务库 {
    class: entity
    direction: down

    biz: 业务表 {
      class: entity
      grid-columns: 3

      factors: 因子
      strategies: 策略
      mining_tasks: 挖掘任务
      results: 回测结果
      users: 用户
    }

    narrow: 行情/宏观窄表 {
      class: entity
      grid-columns: 3

      stock_daily: stock_daily
      etf_daily: etf_daily
      macro: macro_indicator
      financial: financial_indicator
      stock_index: stock_index
    }
  }

  qlib: qlib bin 行情库 {
    class: entity
    grid-columns: 2

    features: "features/{code}/{field}.day.bin"
    calendars: calendars/day.txt
    instruments: instruments/*.txt
    content: A股日K / 指数 / ETF / 宏观因子
  }
}

# ============================================
# 跨层连接
# ============================================

# 前端 → API
frontend.http -> api.rest: REST 请求
frontend.ws -> api.core.ws_mgr: WebSocket 推送

# API → 服务
api.core.io_exec -> service.business: IO 密集任务派发
api.core.cpu_exec -> service.business: CPU 密集任务派发
api.core.scheduler -> service.sync_workers: 定时触发同步

# API 直连数据库
api.core.db -> data.pg: 异步数据库访问

# 服务 → 数据
service.business.factor_eval -> data.pg: 读写业务数据
service.business.backtest_engine -> data.qlib: 读写行情数据
service.sync_workers -> data.pg: 写入同步数据
service.sync_workers -> data.qlib: 写入行情数据

# 进度桥接 (文件间接通信 → 虚线)
service.sync_workers.progress -> api.core.ws_mgr: 进度文件桥接 {
  style.stroke-dash: 5
}

# ============================================
# 关键链路说明
# ============================================
note: |`md
  **关键链路**
  前端 → FastAPI API → 服务层 (执行器派发 CPU/IO 密集计算) → PostgreSQL / qlib bin

  **同步链路**
  同步 Worker 子进程独立拉取外部数据 → 写入双存储 → 进度经文件桥接回传前端

  **设计要点**
  ★ 双存储: PostgreSQL 业务库 + qlib bin 行情库并行
  ★ 独立子进程: 每类同步一个进程, start_new_session 隔离运行
`|
