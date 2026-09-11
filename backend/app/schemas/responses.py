"""第一批 response_model 契约模型（market / factor / logs 高频 GET）。

建模原则：
- 信封统一 ApiResponse[T]（见 schemas/common.py，已 Generic 化）；
- data 内模型穷举当前实际返回的全部字段（golden-keys 测试见
  tests/test_response_contract.py，字段丢一个都会红）；
- 形状动态/第三方透传的 payload 用 ConfigDict(extra="allow") 宽松模型保字段，
  并在 docstring 注明"宽松模型，待收紧"；
- 数值可空字段一律 `| None = None`，不加会改变值的 serializer；
- 字段名与现有响应逐字一致，前端消费精确字段名，不得改动。
"""
from typing import Any

from pydantic import BaseModel, ConfigDict

# ---------------- market ----------------


class IndexItem(BaseModel):
    """/market/indices 条目。"""

    code: str
    name: str
    desc: str
    qlib_code: str


class IndexListData(BaseModel):
    items: list[IndexItem]


class QuoteItem(BaseModel):
    """/market/overview 行情条目（price/pct 来自收盘价序列，恒有值）。"""

    code: str
    name: str
    price: float | None = None
    # 单收盘时 pct_change 是 int 0，其余是 float —— 用智能联合保住原始 JSON 类型
    pct_change: float | int | None = None


class OverviewData(BaseModel):
    items: list[QuoteItem]


class KlineBar(BaseModel):
    """宽松模型，待收紧：K 线条目由 qlib 字段聚合而来，extra="allow" 兜底透传。"""

    model_config = ConfigDict(extra="allow")

    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    # 首条 pct_change 是 NaN→0（int），后续为 float
    pct_change: float | int | None = None


class KlineData(BaseModel):
    index_code: str
    index_name: str
    period: str
    count: int
    items: list[KlineBar]


# ---------------- factor ----------------


class FactorItem(BaseModel):
    """因子库条目：services.factor.library._to_dict 的穷举（19 字段）。

    decay / ic_by_horizon 是 JSON 大字段（list 或 dict，口径随评价版本演变），
    用 Any 保形状不丢。
    """

    id: int
    name: str
    expression: str
    category: str
    description: str | None = None
    ic: float | None = None
    rank_ic: float | None = None
    icir: float | None = None
    ir: float | None = None
    turnover: float | None = None
    decay: Any | None = None
    ic_by_horizon: Any | None = None
    orthogonal_ic: float | None = None
    eval_start: str | None = None
    eval_end: str | None = None
    evaluated_at: str | None = None
    status: str
    source_task_id: int | None = None
    created_at: str | None = None


class FactorListData(BaseModel):
    items: list[FactorItem]
    total: int


# ---------------- logs ----------------


class LogFileInfo(BaseModel):
    """/logs/files 条目（modified 为 unix 秒字符串，文件不存在时为 null）。"""

    name: str
    size: int
    size_human: str
    backup_count: int
    backup_size: int
    backup_size_human: str
    modified: str | None = None


class LogFilesData(BaseModel):
    items: list[LogFileInfo]


class LogEntry(BaseModel):
    """宽松模型，待收紧：日志条目（extra="allow"）。

    只声明 JSON / 文本两种日志格式**都有**的 6 个键；JSON 格式独有的
    detail / worker_kind 走 extra 透传 —— 文本格式缺这两个键，若声明成
    带默认值的字段会被序列化成 null，改变响应 payload。
    """

    model_config = ConfigDict(extra="allow")

    timestamp: str
    level: str
    logger: str
    message: str
    request_id: str
    traceback: str


class LogsData(BaseModel):
    items: list[LogEntry]
    total: int
    file: str
