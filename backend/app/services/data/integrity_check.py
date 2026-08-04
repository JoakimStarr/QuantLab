"""数据完整性校验：通过 qlib 直接加载验证 bin 数据可读性。"""
import logging
import datetime
from app.services.quant.qlib_init import init_qlib

logger = logging.getLogger(__name__)


def check_integrity(provider_uri: str, universe: str = None,
                    start_time: str = "2020-01-01", end_time: str = None) -> dict:
    """通过 qlib 直接加载验证 bin 数据完整性。

    对空数据/异常做友好返回（不抛 500），方便前端展示可操作的信息。

    Returns:
        {
            "ok": bool,
            "rows": int,
            "columns": list,
            "total_stocks": int,
            "summary": str,
            "error": str | None,   # 异常时返回错误信息
        }
    """
    if end_time is None:
        end_time = datetime.date.today().strftime("%Y-%m-%d")

    try:
        # 初始化 qlib
        init_qlib()

        from qlib.data import D
        # D.instruments 返回 dict（instrument -> 起止日期），转成代码列表传入 D.features
        inst = D.instruments(market=universe or "all")
        codes = list(inst.keys()) if isinstance(inst, dict) else []
        if not codes:
            # 尝试用 list_instruments 转换（某些 qlib 版本返回特殊对象）
            try:
                code_map = D.list_instruments(inst, freq="day")
                codes = list(code_map.keys())
            except Exception:
                codes = []
        if not codes:
            return {
                "ok": False, "rows": 0, "columns": [], "total_stocks": 0,
                "summary": "qlib 未加载到任何股票（instruments 为空），请检查数据同步状态",
                "error": "instruments 为空：可能日历 day.txt 为空或股票池文件未同步",
            }

        # 限定验证范围：只取一部分股票，避免全市场加载过慢/内存过大
        sample = codes[:50]
        df = D.features(sample, ["$close", "$open"], start_time=start_time, end_time=end_time)

        if df is None or df.empty:
            return {
                "ok": False, "rows": 0, "columns": [], "total_stocks": len(sample),
                "summary": "qlib 加载数据为空（bin 数据与日历不匹配或尚无数据）",
                "error": f"在 {start_time}~{end_time} 区间未读到行情数据，请检查回填是否完成",
            }

        total_stocks = df.index.get_level_values("instrument").nunique()
        rows = len(df)
        return {
            "ok": True,
            "rows": rows,
            "columns": list(df.columns),
            "total_stocks": total_stocks,
            "summary": f"qlib 加载验证: {total_stocks} 只股票, {rows} 行数据",
            "error": None,
        }
    except Exception as e:
        logger.exception("完整性校验异常")
        return {
            "ok": False, "rows": 0, "columns": [], "total_stocks": 0,
            "summary": f"完整性校验失败: {e}",
            "error": str(e)[:300],
        }
