"""Prometheus 指标收集与 /metrics 端点。

指标清单：
- http_requests_total: HTTP 请求计数（method, path, status）
- http_request_duration_seconds: HTTP 请求延迟直方图
- ws_active_connections: WebSocket 当前活跃连接数
- mining_tasks_total: 挖掘任务计数（type, status）
- backtest_tasks_total: 回测任务计数（status）
- db_pool_size: 数据库连接池当前大小
- factor_library_total: 因子库总数（gauge，定时更新）
"""
import logging

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# --- HTTP 指标 ---
http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求延迟（秒）",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

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

# --- 系统指标 ---
db_pool_size = Gauge(
    "db_pool_size",
    "数据库连接池当前使用连接数",
)
scheduler_running = Gauge(
    "scheduler_running",
    "调度器是否运行中（1=运行, 0=停止）",
)


def record_http_request(method: str, path: str, status: int, duration: float) -> None:
    """记录一次 HTTP 请求的指标。"""
    # 归一化路径（去掉 path params，避免高基数）
    normalized = _normalize_path(path)
    http_requests_total.labels(method=method, path=normalized, status=str(status)).inc()
    http_request_duration_seconds.labels(
        method=method, path=normalized
    ).observe(duration)


def _normalize_path(path: str) -> str:
    """归一化路径，避免 path params 造成高基数。"""
    # /api/v1/factor/123 -> /api/v1/factor/:id
    parts = path.strip("/").split("/")
    normalized = []
    for part in parts:
        if part.isdigit() or (len(part) > 10 and part.replace("-", "").isalnum()):
            normalized.append(":id")
        else:
            normalized.append(part)
    return "/" + "/".join(normalized)


# --- FastAPI 路由 ---
router = APIRouter(tags=["monitoring"])


@router.get("/metrics")
async def metrics():
    """Prometheus metrics 端点。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
