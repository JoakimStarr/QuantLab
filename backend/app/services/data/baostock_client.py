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

连接生命周期约定（重要，防止账号被 baostock 风控拉黑）:
- baostock 对"同一 IP 同时在线连接数 / 登录频率"有限制，未登出即被杀会泄漏服务端会话。
- 爬取进程（app.services.data.sync_worker）必须用 ``baostock_session()`` 上下文包裹整个
  爬取流程：进入时 login，退出时 finally 必然 logout，配合 worker 的 SIGTERM handler，
  保证"无论如何先 logout 再退出进程"。SIGKILL 无法拦截，但由服务端超时自动回收兜底。
"""
import asyncio
import atexit
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# baostock 官方访问约束（https://www.baostock.com/blacklist）：
#   - 每日 API 请求不超过 5 万次，超过后进入黑名单
#   - 禁止并发连接访问
# 这里用每日请求计数（跨进程文件锁）+ 单线程执行器做硬保护。
# ============================================================
DAILY_REQUEST_LIMIT = 50_000


class BaostockQuotaError(RuntimeError):
    """当日 baostock 请求数已达上限，应中止本次爬取（避免无谓重试）。"""


_login_lock = threading.Lock()
_logged_in = False
# 单 worker：baostock 禁止并发连接，即使未来有人误调 async 版 fetch 也不会并发
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="baostock")

_request_count_lock = threading.Lock()


def _request_count_file() -> Path:
    """请求计数文件（与 sync.lock 同目录，跨进程共享）。"""
    from app.core.config import settings
    return Path(settings.PROJECT_ROOT) / "data" / "baostock_requests.json"


def _consume_request_slot() -> None:
    """消耗一个当日请求配额；已达上限则抛 BaostockQuotaError。

    用 ``fcntl.flock`` 保证跨进程读写安全（爬取在独立 worker 子进程运行）。
    """
    import fcntl

    today = time.strftime("%Y-%m-%d")
    path = _request_count_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _request_count_lock:
        with open(path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read().strip()
                data = json.loads(content) if content else {}
                if data.get("date") != today:
                    data = {"date": today, "count": 0}
                if data["count"] >= DAILY_REQUEST_LIMIT:
                    raise BaostockQuotaError(
                        f"baostock 当日请求数已达上限 {DAILY_REQUEST_LIMIT}，"
                        "请明天再试或降低同步频率"
                    )
                data["count"] += 1
                f.seek(0)
                f.truncate()
                json.dump(data, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


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


def ensure_logout():
    """幂等登出：仅在已登录时调用 bs.logout()，线程安全。

    由爬取进程在 finally / 信号 handler 中调用，确保退出进程前释放服务端会话。

    baostock 服务端异常时（数据查询不响应），logout 也可能阻塞在 socket 读上。
    因此把 bs.logout() 放入 **daemon 线程** 执行——即使卡住也不阻塞 worker 退出
    （否则 worker 卡在登出、爬取锁不释放，后续所有 baostock 同步全被挡）。
    """
    global _logged_in
    with _login_lock:
        if not _logged_in:
            return
        _logged_in = False

    def _do_logout():
        import baostock as bs
        try:
            bs.logout()
            logger.info("baostock logout OK")
        except Exception as e:  # noqa: BLE001
            logger.warning("baostock logout 异常: %s", e)

    threading.Thread(target=_do_logout, daemon=True, name="baostock-logout").start()


def _logout():
    """登出（atexit 注册，兜底）。"""
    ensure_logout()


atexit.register(_logout)


class baostock_session:
    """baostock 登录会话上下文管理器。

    用法（爬取进程入口）::

        with baostock_session():
            await run_baostock_backfill(...)   # 整个爬取流程

    - __enter__: 幂等登录
    - __exit__: **无条件** logout（正常/异常/被信号中断都会执行），
      保证退出进程前连接已归还服务端。
    """

    def __enter__(self):
        _ensure_login()
        return self

    def __exit__(self, exc_type, exc, tb):
        ensure_logout()
        return False


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
        _consume_request_slot()
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
        _consume_request_slot()
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
    _consume_request_slot()
    _ensure_login()
    import baostock as bs
    rs = bs.query_daily_history_k_AStock(date=date)
    if rs.error_code != '0':
        raise RuntimeError(f"query_daily_history_k_AStock failed: {rs.error_code} {rs.error_msg}")
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    return pd.DataFrame(data_list, columns=rs.fields)


def fetch_etf_daily_sync(date: str) -> pd.DataFrame:
    """同步获取某日全市场 ETF 日 K 线（一次请求返回全部 ETF）。

    与 ``fetch_daily_all_a_stock_sync`` 同构：按交易日拉全市场，code 为 baostock
    格式（如 sh.510300），用 ``from_baostock_code`` 转 qlib 格式。

    Args:
        date: 日期字符串 'YYYY-MM-DD'

    Returns:
        DataFrame, 列: date,code,open,high,low,close,preclose,volume,amount,
                      adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST
    Raises:
        RuntimeError: baostock 调用失败
    """
    _consume_request_slot()
    _ensure_login()
    import baostock as bs
    rs = bs.query_daily_history_k_ETF(date=date)
    if rs.error_code != '0':
        raise RuntimeError(f"query_daily_history_k_ETF failed: {rs.error_code} {rs.error_msg}")
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    return pd.DataFrame(data_list, columns=rs.fields)


def fetch_stock_history_sync(code: str, start_date: str, end_date: str,
                             frequency: str = "d", adjustflag: str = "3") -> pd.DataFrame:
    """同步版。"""
    _consume_request_slot()
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
