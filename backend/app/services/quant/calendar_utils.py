"""交易日历工具：基于 exchange_calendars 的 A 股（XSHG/XSHE）交易日历。

解决回测/滚动 IC 中"日期对齐"问题：
- 按真实交易日（含节假日）生成日期序列，替代 pd.date_range(freq='D') 自然日
- 提供交易日判断、前后交易日、交易日对齐等工具

exchange_calendars 不可用（未安装/加载失败）时自动降级为工作日近似（周一~周五），
保证平台在无该依赖的环境下仍可用。
"""
import logging
from functools import lru_cache

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CALENDAR = "XSHG"  # 上交所（与深交所休市规则一致，覆盖 A 股全部标的）

_calendar = None
_calendar_available = False


def _load_calendar():
    """惰性加载 exchange_calendars 的 A 股日历（进程内单例）。"""
    global _calendar, _calendar_available
    if _calendar is not None:
        return _calendar
    try:
        import exchange_calendars as xcals

        _calendar = xcals.get_calendar(_DEFAULT_CALENDAR)
        _calendar_available = True
        logger.info("交易日历已加载: %s (exchange_calendars)", _DEFAULT_CALENDAR)
    except Exception as e:
        _calendar_available = False
        logger.warning("exchange_calendars 不可用（%s），交易日历降级为工作日近似", e)
    return _calendar


def is_calendar_available() -> bool:
    """exchange_calendars 是否可用。"""
    _load_calendar()
    return _calendar_available


def get_trading_days(start, end) -> pd.DatetimeIndex:
    """返回 [start, end] 闭区间内的交易日序列（升序，归一化到日期）。

    Args:
        start/end: 日期字符串或 datetime

    Returns:
        pd.DatetimeIndex（normalize 后，不含时间部分）
    """
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if start > end:
        return pd.DatetimeIndex([])

    cal = _load_calendar()
    if cal is not None:
        try:
            days = cal.sessions_in_range(start, end)
            return pd.DatetimeIndex(days)
        except Exception as e:
            logger.warning("交易日历查询失败，降级工作日: %s", e)

    # 降级：工作日近似（不包含法定节假日）
    return pd.date_range(start, end, freq="B")


def is_trading_day(date) -> bool:
    """判断是否为交易日（exchange_calendars 含法定节假日规则）。"""
    d = pd.Timestamp(date).normalize()
    cal = _load_calendar()
    if cal is not None:
        try:
            return cal.is_session(d)
        except Exception:
            return d.dayofweek < 5
    return d.dayofweek < 5


def next_trading_day(date, n: int = 1):
    """date 之后第 n 个交易日（n=1 表示下一个交易日；date 本身是交易日也不算）。"""
    cal = _load_calendar()
    if cal is not None:
        try:
            return cal.date_to_session(pd.Timestamp(date).normalize(), direction="next", offset=n)
        except Exception:
            pass
    days = get_trading_days(pd.Timestamp(date).normalize() + pd.Timedelta(days=1),
                            pd.Timestamp(date).normalize() + pd.Timedelta(days=7 * (n + 2)))
    return days[n - 1] if len(days) >= n else days[-1]


def prev_trading_day(date, n: int = 1):
    """date 之前第 n 个交易日（n=1 表示上一个交易日）。"""
    cal = _load_calendar()
    if cal is not None:
        try:
            return cal.date_to_session(pd.Timestamp(date).normalize(), direction="previous", offset=n)
        except Exception:
            pass
    days = get_trading_days(pd.Timestamp(date).normalize() - pd.Timedelta(days=7 * (n + 2)),
                            pd.Timestamp(date).normalize() - pd.Timedelta(days=1))
    return days[-n] if len(days) >= n else days[0]


def offset_trading_days(date, n: int):
    """交易日偏移：n>0 向后（未来），n<0 向前（过去）；偏移不包含 date 自身。"""
    if n > 0:
        return next_trading_day(date, n=n)
    if n < 0:
        return prev_trading_day(date, n=-n)
    return pd.Timestamp(date).normalize()


def align_dates_to_trading_days(dates) -> pd.DatetimeIndex:
    """把任意日期列表对齐为"最近的"交易日序列（去重、升序、过滤非交易日）。

    非交易日按"向前看最近的交易日"处理（如周六→下周一）。
    """
    if len(dates) == 0:
        return pd.DatetimeIndex([])
    s = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    cal = _load_calendar()
    if cal is None:
        return s[s.dayofweek < 5].unique()
    try:
        sessions = cal.sessions_in_range(s.min(), s.max())
        return sessions
    except Exception:
        return s[s.dayofweek < 5].unique()


@lru_cache(maxsize=128)
def trading_days_cached(start: str, end: str) -> tuple:
    """带缓存的交易日序列（返回 tuple 以便缓存，用于频繁调用的场景）。"""
    return tuple(get_trading_days(start, end))
