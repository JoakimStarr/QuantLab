"""数据完整性校验：检测 qlib bin 文件长度与日历天数一致性。"""
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

QLIB_BIN_HEADER_SIZE = 4


def check_integrity(provider_uri: str, universe: str = None) -> dict:
    """校验 qlib bin 数据完整性。

    Returns:
        {
            "ok": bool,
            "calendar_days": int,
            "total_stocks": int,
            "valid_stocks": int,
            "stocks_missing_fields": int,
            "stocks_all_nan": int,
            "length_mismatches": int,
            "missing_files": int,
            "issues": [...],
            "all_nan_stocks": [...],
            "summary": str,
        }
    """
    base = Path(provider_uri)

    # 读取日历
    cal_path = base / "calendars" / "day.txt"
    if not cal_path.exists():
        return {"ok": False, "error": "日历文件不存在"}
    with open(cal_path, "r") as f:
        calendar = [l.strip() for l in f if l.strip()]
    expected_len = len(calendar)

    # 读取股票池（如果指定）
    stock_filter = None
    if universe:
        pool_file = base / "instruments" / f"{universe}.txt"
        if pool_file.exists():
            with open(pool_file, "r") as f:
                entries = set()
                for line in f:
                    parts = line.strip().split("\t")
                    if parts:
                        entries.add(parts[0].lower())
                stock_filter = entries

    # 遍历所有股票
    features_dir = base / "features"
    if not features_dir.exists():
        return {"ok": False, "error": "features 目录不存在"}

    expected_fields = ["open", "high", "low", "close", "volume"]

    issues = []
    missing_fields_stocks = []
    all_nan_stocks = []
    total = 0
    valid = 0

    for stock_dir in sorted(features_dir.iterdir()):
        if not stock_dir.is_dir():
            continue
        code = stock_dir.name

        # 过滤
        if stock_filter and code.lower() not in stock_filter:
            continue

        total += 1

        # 检查每个字段
        has_all_fields = True
        all_nan = True
        field_lens = {}

        for field in expected_fields:
            bin_path = stock_dir / f"{field}.day.bin"
            if not bin_path.exists():
                has_all_fields = False
                issues.append({
                    "code": code, "field": field,
                    "issue": "file_missing",
                    "expected": expected_len, "actual": 0,
                })
                continue

            # 读取 bin 文件
            file_size = bin_path.stat().st_size
            data_len = (file_size - QLIB_BIN_HEADER_SIZE) // 4  # float32 = 4 bytes

            if data_len != expected_len:
                issues.append({
                    "code": code, "field": field,
                    "issue": "length_mismatch",
                    "expected": expected_len, "actual": data_len,
                })

            field_lens[field] = data_len

            # 检查是否有有效值（非全 NaN）
            with open(bin_path, "rb") as f:
                f.read(QLIB_BIN_HEADER_SIZE)
                data = np.fromfile(f, dtype="<f4", count=min(data_len, expected_len))
            if data.size > 0 and np.any(~np.isnan(data)):
                all_nan = False

        if not has_all_fields:
            missing_fields_stocks.append(code)
        if all_nan:
            all_nan_stocks.append(code)
        if has_all_fields and not all_nan:
            valid += 1

    # 汇总
    length_issues = [i for i in issues if i["issue"] == "length_mismatch"]
    missing_issues = [i for i in issues if i["issue"] == "file_missing"]

    return {
        "ok": True,
        "calendar_days": expected_len,
        "total_stocks": total,
        "valid_stocks": valid,
        "stocks_missing_fields": len(missing_fields_stocks),
        "stocks_all_nan": len(all_nan_stocks),
        "length_mismatches": len(length_issues),
        "missing_files": len(missing_issues),
        "issues": issues[:100],  # 最多返回100条
        "all_nan_stocks": all_nan_stocks[:50],
        "summary": f"校验完成: {total} 只股票, {valid} 只有效, "
                   f"{len(missing_fields_stocks)} 只缺字段, "
                   f"{len(all_nan_stocks)} 只全NaN, "
                   f"{len(length_issues)} 个长度不匹配",
    }
