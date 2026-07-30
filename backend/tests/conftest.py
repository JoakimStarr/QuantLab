"""pytest 全局 fixture。"""
import pytest
import numpy as np
import struct
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def tmp_qlib_data(tmp_path):
    """创建临时 qlib bin 数据用于测试（mock 小型 bin 文件）。"""
    base = tmp_path / "qlib_test"
    cal_dir = base / "calendars"
    feat_dir = base / "features"
    cal_dir.mkdir(parents=True)
    feat_dir.mkdir(parents=True)

    # 日历 (10 个交易日)
    dates = [f"2024-01-{i:02d}" for i in range(1, 11)]
    with open(cal_dir / "day.txt", "w") as f:
        for d in dates:
            f.write(d + "\n")

    # 3 只股票的 OHLCV 数据
    stocks = ["sh600000", "sz000001", "sz300001"]
    for stock in stocks:
        sdir = feat_dir / stock
        sdir.mkdir()
        for field in ["open", "high", "low", "close", "volume"]:
            data = np.abs(np.random.randn(10).astype(np.float32) * 10 + 50)
            if field == "volume":
                data = data * 100000
            with open(sdir / f"{field}.day.bin", "wb") as f:
                f.write(struct.pack("<f", 0.0))  # start_index
                data.tofile(f)

    return base


@pytest.fixture
def mock_db_session():
    """Mock 数据库 session。"""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def tmp_workdir(tmp_path):
    """临时工作目录。"""
    return tmp_path
