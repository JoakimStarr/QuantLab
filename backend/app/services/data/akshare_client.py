"""AKShare 数据客户端：A 股行情与新闻（量化平台专用）。"""
import asyncio
import pandas as pd
from functools import partial
from app.core.errors import DataFetchError


async def fetch_data(func, *args, max_retries=2, timeout=20, **kwargs):
    """在线程池中运行同步 AKShare 函数，带重试与超时。"""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            loop = asyncio.get_running_loop()
            fn = partial(func, *args, **kwargs)
            result = await asyncio.wait_for(
                loop.run_in_executor(None, fn),
                timeout=timeout
            )
            if result is None or (isinstance(result, pd.DataFrame) and result.empty):
                raise ValueError("empty result")
            return result
        except asyncio.TimeoutError:
            last_error = TimeoutError(f"fetch timeout after {timeout}s")
        except Exception as e:
            last_error = e
    raise DataFetchError(f"数据获取失败: {last_error}")


def get_stock_news(symbol: str) -> pd.DataFrame:
    """获取个股新闻。"""
    import akshare as ak
    return ak.stock_news_em(symbol=symbol)
