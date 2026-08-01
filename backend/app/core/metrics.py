"""Prometheus 指标收集与 /metrics 端点。

使用 prometheus-fastapi-instrumentator 自动完成 HTTP 埋点，
自定义业务指标通过 prometheus_client 手动注册。

指标清单：
- ws_active_connections: WebSocket 当前活跃连接数
- mining_tasks_total: 挖掘任务计数（type, status）
- backtest_tasks_total: 回测任务计数（status）
- factor_eval_duration_seconds: 因子评价耗时
- factor_eval_total: 因子评价总数（status）
- llm_call_duration_seconds: LLM 调用耗时
- llm_call_total: LLM 调用总数（provider, status）
- cache_hit_total: 缓存命中总数（cache_name）
- cache_miss_total: 缓存未命中总数（cache_name）
- data_sync_duration_seconds: 数据同步耗时（source）
- data_sync_total: 数据同步次数（source, status）
- db_pool_size: 数据库连接池当前大小
- factor_library_total: 因子库总数（gauge，定时更新）
"""
import logging

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)


def setup_metrics(app) -> None:
    """使用 prometheus-fastapi-instrumentator 自动注册 HTTP 指标并暴露 /metrics 端点。"""
    Instrumentator().instrument(app).expose(app)


# --- WebSocket 指标 ---
ws_active_connections = Gauge(
    "ws_active_connections",
    "WebSocket 当前活跃连接数",
)

# --- 业务指标 ---
mining_tasks_total = Counter(
    "mining_tasks_total",
    "挖掘任务总数",
    ["type", "status"],
)
backtest_tasks_total = Counter(
    "backtest_tasks_total",
    "回测任务总数",
    ["status"],
)
factor_library_total = Gauge(
    "factor_library_total",
    "因子库总数",
)

# --- 因子评价指标 ---
eval_duration = Histogram(
    "factor_eval_duration_seconds",
    "因子评价耗时",
    buckets=[1, 5, 10, 30, 60, 120],
)
eval_total = Counter(
    "factor_eval_total",
    "因子评价总数",
    ["status"],
)

# --- LLM 调用指标 ---
llm_call_duration = Histogram(
    "llm_call_duration_seconds",
    "LLM 调用耗时",
)
llm_call_total = Counter(
    "llm_call_total",
    "LLM 调用总数",
    ["provider", "status"],
)

# --- 缓存指标 ---
cache_hit_total = Counter(
    "cache_hit_total",
    "缓存命中总数",
    ["cache_name"],
)
cache_miss_total = Counter(
    "cache_miss_total",
    "缓存未命中总数",
    ["cache_name"],
)

# --- 数据同步指标 ---
sync_duration = Histogram(
    "data_sync_duration_seconds",
    "数据同步耗时",
    ["source"],
)
sync_total = Counter(
    "data_sync_total",
    "数据同步次数",
    ["source", "status"],
)

# --- 系统指标 ---
db_pool_size = Gauge(
    "db_pool_size",
    "数据库连接池当前使用连接数",
)
db_pool_available = Gauge(
    "db_pool_available",
    "数据库连接池可用连接数",
)
db_pool_overflow = Gauge(
    "db_pool_overflow",
    "数据库连接池溢出连接数",
)
scheduler_running = Gauge(
    "scheduler_running",
    "调度器是否运行中（1=运行, 0=停止）",
)