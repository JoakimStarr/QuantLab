"""AKShare 数据客户端：A 股行情与新闻（量化平台专用）。"""
import asyncio
import json
import logging
import pandas as pd
from functools import partial
from app.core.errors import DataFetchError

logger = logging.getLogger(__name__)


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


def _stock_news_em_fixed(symbol: str = "603777") -> pd.DataFrame:
    """直接请求东方财富个股新闻 API，绕过 akshare 1.18.63 的 r"\\u3000" 正则 bug。

    akshare 原始实现在 news_stock.py:116 使用 r"\\u3000"（raw 字符串），
    pyarrow 正则引擎拒绝 \\u 转义，抛出 ArrowInvalid。
    这里改为用真实 unicode 字符 "\\u3000"（全角空格）做字面替换。
    """
    from curl_cffi import requests

    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner_param = {
        "uid": "",
        "keyword": symbol,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 10,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": "jQuery35101792940631092459_1764599530165",
        "param": json.dumps(inner_param, ensure_ascii=False),
        "_": "1764599530176",
    }
    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en,zh-CN;q=0.9,zh;q=0.8",
        "cache-control": "no-cache",
        "connection": "keep-alive",
        "host": "search-api-web.eastmoney.com",
        "referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",  # noqa: E501

    }
    r = requests.get(url, params=params, headers=headers, timeout=15)
    data_text = r.text
    # 解析 JSONP 包裹
    prefix = "jQuery35101792940631092459_1764599530165("
    data_json = json.loads(data_text.strip(prefix)[:-1])
    temp_df = pd.DataFrame(data_json["result"]["cmsArticleWebOld"])
    temp_df["url"] = "http://finance.eastmoney.com/a/" + temp_df["code"] + ".html"
    temp_df.rename(
        columns={
            "date": "发布时间",
            "mediaName": "文章来源",
            "code": "-",
            "title": "新闻标题",
            "content": "新闻内容",
            "url": "新闻链接",
            "image": "-",
        },
        inplace=True,
    )
    temp_df["关键词"] = symbol
    temp_df = temp_df[
        ["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"]
    ]
    # 清理 <em> 高亮标签（用字面替换，不用 regex，避免 pyarrow 转义问题）
    for col in ("新闻标题", "新闻内容"):
        temp_df[col] = (
            temp_df[col]
            .str.replace("(<em>", "", regex=False)
            .str.replace("</em>)", "", regex=False)
            .str.replace("<em>", "", regex=False)
            .str.replace("</em>", "", regex=False)
        )
    # 修复点：用真实 unicode 字符 U+3000（全角空格）做字面替换，而非 raw string r"\u3000"
    temp_df["新闻内容"] = temp_df["新闻内容"].str.replace("\u3000", "", regex=False)
    temp_df["新闻内容"] = temp_df["新闻内容"].str.replace("\r\n", " ", regex=False)
    return temp_df


def get_stock_news(symbol: str) -> pd.DataFrame:
    """获取个股新闻。

    优先用修复版（绕过 akshare 1.18.63 的 pyarrow 正则 bug），
    失败则回退到 akshare 原始接口（可能在旧版 pandas/pyarrow 下可用）。
    """
    # 优先使用修复版
    try:
        return _stock_news_em_fixed(symbol)
    except Exception as e:
        logger.warning("修复版新闻拉取失败 %s: %s，回退到 akshare 原始接口", symbol, e)
    # 回退到 akshare 原始接口
    import akshare as ak
    return ak.stock_news_em(symbol=symbol)
