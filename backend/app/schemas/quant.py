from typing import Optional
from pydantic import BaseModel


class SyncDataRequest(BaseModel):
    """触发股票数据同步到 qlib bin"""
    start_date: Optional[str] = None  # 默认 default_backtest_period.start
    end_date: Optional[str] = None    # 默认今天
    codes: Optional[list[str]] = None  # 默认 config.universe
    universe: Optional[str] = None     # 覆盖 config.universe
    days: Optional[int] = None         # akshare增量同步天数（默认30）
    years: Optional[int] = None        # baostock 全量回填年数（从最新向旧）


class QlibStatusResponse(BaseModel):
    available: bool
    message: Optional[str] = None
    provider_uri: Optional[str] = None
