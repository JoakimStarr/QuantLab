"""baostock 客户端封装。

baostock 是同步阻塞 API 且需要 login/logout，本模块提供：
- 单例 login/logout 管理（首次调用自动 login, atexit 自动 logout）
- 线程池执行同步 baostock 函数
- 全市场某日 K 线 (query_daily_all_a_stock)
- 单股历史 K 线 (query_history_k_data_by_code)
- 代码转换工具

baostock 优势:
- 一次返回全市场某日数据 (akshare 需逐只爬)
- 自带 isST 字段 (解决 ST 股 5% 涨跌停 mask)
- 自带 peTTM/pbMRQ/psTTM/pcfNcfTTM 估值字段
"""
import asyncio
import atexit
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

logger = logging.getLogger(__name__)

_login_lock = threading.Lock()
_logged_in = False
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="baostock")


def _ensure_login():
    """确保已登录（线程安全，幂等）。"""
    global _logged_in
    with _login_lock:
        if _logged_in:
            return
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")
        logger.info("baostock login OK")
        _logged_in = True


def _logout():
    """登出（atexit 注册）。"""
    global _logged_in
    with _login_lock:
        if _logged_in:
            import baostock as bs
            bs.logout()
            _logged_in = False
            logger.info("baostock logout OK")


atexit.register(_logout)


async def _run_sync(func, *args, **kwargs):
    """在线程池中运行同步 baostock 函数。"""
    loop = asyncio.get_event_loop()

    def wrapper():
        _ensure_login()
        return func(*args, **kwargs)
    return await loop.run_in_executor(_executor, wrapper)


async def fetch_daily_all_a_stock(date: str) -> pd.DataFrame:
    """获取某日全市场 A 股日 K 线（含 ST 标记和估值字段）。

    Args:
        date: 日期字符串 'YYYY-MM-DD'
    Returns:
        DataFrame, 列: date,code,open,high,low,close,preclose,volume,amount,
                      adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST
    Raises:
        RuntimeError: baostock 调用失败
    """
    import baostock as bs

    def fetch():
        rs = bs.query_daily_history_k_AStock(date=date)
        if rs.error_code != '0':
            raise RuntimeError(f"query_daily_history_k_AStock failed: {rs.error_code} {rs.error_msg}")
        data_list = []
        while (rs.error_code == '0') and rs.next():
            data_list.append(rs.get_row_data())
        return pd.DataFrame(data_list, columns=rs.fields)
    return await _run_sync(fetch)


async def fetch_stock_history(code: str, start_date: str, end_date: str,
                              frequency: str = "d", adjustflag: str = "3") -> pd.DataFrame:
    """获取单只股票历史 K 线。
    Args:
        code: baostock 代码格式 'sh.600000' / 'sz.000001' / 'bj.430047'
        start_date/end_date: 'YYYY-MM-DD'
        frequency: 'd'=日 'w'=周 'm'=月
        adjustflag: '1'=后复权 '2'=前复权 '3'=不复权
    Returns:
        DataFrame, 列同 fetch_daily_all_a_stock
    """
    import baostock as bs

    def fetch():
        rs = bs.query_history_k_data_plus(
            code=code,
            fields="date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST",  # noqa: E501
            start_date=start_date, end_date=end_date,
            frequency=frequency, adjustflag=adjustflag,
        )
        if rs.error_code != '0':
            raise RuntimeError(f"query_history_k_data_plus failed for {code}: {rs.error_code} {rs.error_msg}")
        data_list = []
        while (rs.error_code == '0') and rs.next():
            data_list.append(rs.get_row_data())
        return pd.DataFrame(data_list, columns=rs.fields)
    return await _run_sync(fetch)


def to_baostock_code(qlib_code: str) -> str:
    """QLib 代码转 baostock 代码: sh600000 → sh.600000"""
    if '.' in qlib_code:
        return qlib_code
    if len(qlib_code) < 8:
        raise ValueError(f"无效的 qlib 代码: {qlib_code}")
    return f"{qlib_code[:2]}.{qlib_code[2:]}"


def from_baostock_code(bs_code: str) -> str:
    """baostock 代码转 QLib 代码: sh.600000 → sh600000"""
    return bs_code.replace('.', '')


# 同步便捷接口
def fetch_daily_all_a_stock_sync(date: str) -> pd.DataFrame:
    """同步版（给 sync_runner 等同步代码用）。"""
    _ensure_login()
    import baostock as bs
    rs = bs.query_daily_history_k_AStock(date=date)
    if rs.error_code != '0':
        raise RuntimeError(f"query_daily_history_k_AStock failed: {rs.error_code} {rs.error_msg}")
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    return pd.DataFrame(data_list, columns=rs.fields)


def fetch_stock_history_sync(code: str, start_date: str, end_date: str,
                             frequency: str = "d", adjustflag: str = "3") -> pd.DataFrame:
    """同步版。"""
    _ensure_login()
    import baostock as bs
    rs = bs.query_history_k_data_plus(
        code=code,
        fields="date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST",  # noqa: E501
        start_date=start_date, end_date=end_date,
        frequency=frequency, adjustflag=adjustflag,
    )
    if rs.error_code != '0':
        raise RuntimeError(f"query_history_k_data_plus failed for {code}: {rs.error_code} {rs.error_msg}")
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    return pd.DataFrame(data_list, columns=rs.fields)
