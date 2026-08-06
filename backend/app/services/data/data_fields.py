"""qlib bin 字段清单与列名映射（集中定义，供同步/校验/修复模块复用）。

- ``STOCK_BIN_FIELDS``：股票 bin 写入字段（baostock 全部日线字段 + 派生字段）。
- ``BAOSTOCK_DAILY_COL_MAP``：baostock 日线列名 → stock_daily/qlib 字段名。
- ``STOCK_DB_TO_SRC_COL``：``BAOSTOCK_DAILY_COL_MAP`` 的逆映射（DB 列 → baostock 列）。
- ``ETF_BIN_FIELDS``：ETF bin 字段（OHLCV + amount + 派生字段，无股票专属字段）。
- ``INDEX_FIELDS``：指数字段（仅 OHLCV）。
"""
from __future__ import annotations

# qlib bin 写入字段（baostock 全部日线字段；is_st/tradestatus/adjustflag 存为 float 0/1 或数值）
STOCK_BIN_FIELDS: list[str] = [
    "open", "high", "low", "close", "preclose",
    "volume", "amount", "turn",
    "tradestatus", "pct_chg", "is_st",
    "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm",
    "adjustflag",
    "change", "tradable", "factor",
]

# baostock 日线列名 -> stock_daily 列名
BAOSTOCK_DAILY_COL_MAP: dict[str, str] = {
    "open": "open", "high": "high", "low": "low", "close": "close",
    "preclose": "preclose", "volume": "volume", "amount": "amount",
    "turn": "turn", "tradestatus": "tradestatus", "pctChg": "pct_chg",
    "isST": "is_st", "peTTM": "pe_ttm", "pbMRQ": "pb_mrq",
    "psTTM": "ps_ttm", "pcfNcfTTM": "pcf_ncf_ttm", "adjustflag": "adjustflag",
}

# stock_daily 列 -> baostock 风格列名（_DB_TO_SRC_COL 逆映射）
STOCK_DB_TO_SRC_COL: dict[str, str] = {v: k for k, v in BAOSTOCK_DAILY_COL_MAP.items()}

# ETF bin 字段：OHLCV + amount + 衍生字段（无股票专属字段）
ETF_BIN_FIELDS: list[str] = ["open", "high", "low", "close", "volume", "amount",
                             "change", "tradable", "factor"]

# qlib 指数字段（与 chenditc 指数 bin 一致：open/high/low/close/volume）
INDEX_FIELDS: list[str] = ["open", "high", "low", "close", "volume"]
