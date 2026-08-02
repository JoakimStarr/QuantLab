"""数据完整性校验：通过 qlib 直接加载验证 bin 数据可读性。"""
import logging
import datetime
from app.services.quant.qlib_init import init_qlib

logger = logging.getLogger(__name__)


def check_integrity(provider_uri: str, universe: str = None,
                    start_time: str = "2020-01-01", end_time: str = None) -> dict:
    """通过 qlib 直接加载验证 bin 数据完整性。

    Returns:
        {
            "ok": bool,
            "rows": int,
            "columns": list,
            "total_stocks": int,
            "summary": str,
        }
    """
    if end_time is None:
        end_time = datetime.date.today().strftime("%Y-%m-%d")

    # 初始化 qlib
    init_qlib()

    # 用 qlib 直接加载验证
    from qlib.data import D
    inst = D.instruments(market=universe or "all")
    df = D.features(inst, ["$close", "$open"], start_time=start_time, end_time=end_time)

    ok = not df.empty
    total_stocks = df.index.get_level_values("instrument").nunique() if ok else 0
    rows = len(df) if ok else 0

    return {
        "ok": ok,
        "rows": rows,
        "columns": list(df.columns) if ok else [],
        "total_stocks": total_stocks,
        "summary": f"qlib 加载验证: {total_stocks} 只股票, {rows} 行数据",
    }
