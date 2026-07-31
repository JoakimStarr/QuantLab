"""股票代码转换工具。

统一 sh/sz/bj 前缀与 6 位代码之间的转换逻辑。
抽取自 data_adapter / industry_sync / market_data 等模块的重复实现。
"""


def to_qlib_code(code: str) -> str:
    """6 位 AKShare 代码 -> qlib 小写带交易所前缀代码。

    Args:
        code: 6 位股票代码，如 "600000" / "000001" / "430047"

    Returns:
        QLib 格式代码，如 "sh600000"

    Raises:
        ValueError: 代码格式不合法
    """
    code = str(code).strip().zfill(6)
    if code.startswith(("60", "68", "90", "11", "13", "50", "56")):
        return "sh" + code
    if code.startswith(("00", "30", "12", "15", "16", "18")):
        return "sz" + code
    if code.startswith(("83", "87", "43", "92", "88")):
        return "bj" + code
    # 默认按沪市处理
    return "sh" + code
