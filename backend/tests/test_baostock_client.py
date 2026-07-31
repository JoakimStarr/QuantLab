"""baostock_client 单元测试。

不实际调用 baostock API，全部用 unittest.mock 模拟。
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.services.data import baostock_client as client
from app.services.data.baostock_client import (
    fetch_daily_all_a_stock,
    fetch_stock_history,
    to_baostock_code,
    from_baostock_code,
)


def _make_rs(fields, rows, error_code="0", error_msg="ok"):
    """构造 baostock ResultSet 的 mock。"""
    rs = MagicMock()
    rs.error_code = error_code
    rs.error_msg = error_msg
    rs.fields = list(fields)
    rs.next.side_effect = [True] * len(rows) + [False]
    rs.get_row_data.side_effect = list(rows)
    return rs


def _make_bs(rs=None, query_attr="query_daily_history_k_AStock", login_ok=True):
    """构造 baostock 模块 mock。"""
    bs = MagicMock()
    bs.login.return_value.error_code = "0" if login_ok else "1"
    bs.login.return_value.error_msg = "ok" if login_ok else "fail"
    if rs is not None:
        getattr(bs, query_attr).return_value = rs
    return bs


class TestCodeConvert(unittest.TestCase):
    def test_to_baostock_code_normal(self):
        self.assertEqual(to_baostock_code("sh600000"), "sh.600000")
        self.assertEqual(to_baostock_code("sz000001"), "sz.000001")
        self.assertEqual(to_baostock_code("bj430047"), "bj.430047")

    def test_to_baostock_code_idempotent(self):
        self.assertEqual(to_baostock_code("sh.600000"), "sh.600000")

    def test_to_baostock_code_invalid(self):
        with self.assertRaises(ValueError):
            to_baostock_code("sh60000")  # 长度不足 8
        with self.assertRaises(ValueError):
            to_baostock_code("short")

    def test_from_baostock_code_normal(self):
        self.assertEqual(from_baostock_code("sh.600000"), "sh600000")
        self.assertEqual(from_baostock_code("sz.000001"), "sz000001")

    def test_roundtrip(self):
        self.assertEqual(from_baostock_code(to_baostock_code("sh600000")), "sh600000")


class TestEnsureLogin(unittest.TestCase):
    def setUp(self):
        client._logged_in = False

    def tearDown(self):
        client._logged_in = False

    def test_login_idempotent(self):
        mock_bs = _make_bs(login_ok=True)
        with patch.dict("sys.modules", {"baostock": mock_bs}):
            import baostock as bs
            client._ensure_login()
            self.assertTrue(client._logged_in)
            client._ensure_login()
            # 第二次应直接返回，login 只被调用一次
            self.assertEqual(bs.login.call_count, 1)

    def test_login_failure_raises(self):
        mock_bs = _make_bs(login_ok=False)
        with patch.dict("sys.modules", {"baostock": mock_bs}):
            with self.assertRaises(RuntimeError):
                client._ensure_login()
            self.assertFalse(client._logged_in)


class TestFetchDailyAllAStock(unittest.TestCase):
    def setUp(self):
        client._logged_in = False

    def tearDown(self):
        client._logged_in = False

    def test_success(self):
        fields = ["date", "code", "close", "isST"]
        rows = [["2024-01-02", "sh.600000", "10.0", "0"]]
        rs = _make_rs(fields, rows)
        mock_bs = _make_bs(rs=rs, query_attr="query_daily_history_k_AStock")
        with patch.dict("sys.modules", {"baostock": mock_bs}):
            df = asyncio.run(fetch_daily_all_a_stock("2024-01-02"))
        self.assertEqual(list(df.columns), fields)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["code"], "sh.600000")
        mock_bs.query_daily_history_k_AStock.assert_called_once_with(date="2024-01-02")

    def test_failure_raises(self):
        rs = _make_rs(["date"], [["x"]], error_code="1", error_msg="boom")
        mock_bs = _make_bs(rs=rs, query_attr="query_daily_history_k_AStock")
        with patch.dict("sys.modules", {"baostock": mock_bs}):
            with self.assertRaises(RuntimeError):
                asyncio.run(fetch_daily_all_a_stock("2024-01-02"))


class TestFetchStockHistory(unittest.TestCase):
    def setUp(self):
        client._logged_in = False

    def tearDown(self):
        client._logged_in = False

    def test_success(self):
        fields = ["date", "code", "close", "isST"]
        rows = [
            ["2024-01-02", "sh.600000", "10.0", "0"],
            ["2024-01-03", "sh.600000", "11.0", "0"],
        ]
        rs = _make_rs(fields, rows)
        mock_bs = _make_bs(rs=rs, query_attr="query_history_k_data_plus")
        with patch.dict("sys.modules", {"baostock": mock_bs}):
            df = asyncio.run(fetch_stock_history("sh.600000", "2024-01-02", "2024-01-03"))
        self.assertEqual(list(df.columns), fields)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[1]["close"], "11.0")
        mock_bs.query_history_k_data_plus.assert_called_once()
        kwargs = mock_bs.query_history_k_data_plus.call_args.kwargs
        self.assertEqual(kwargs["code"], "sh.600000")
        self.assertEqual(kwargs["start_date"], "2024-01-02")
        self.assertEqual(kwargs["end_date"], "2024-01-03")
        self.assertEqual(kwargs["frequency"], "d")
        self.assertEqual(kwargs["adjustflag"], "3")

    def test_failure_raises(self):
        rs = _make_rs(["date"], [["x"]], error_code="1", error_msg="boom")
        mock_bs = _make_bs(rs=rs, query_attr="query_history_k_data_plus")
        with patch.dict("sys.modules", {"baostock": mock_bs}):
            with self.assertRaises(RuntimeError):
                asyncio.run(fetch_stock_history("sh.600000", "2024-01-02", "2024-01-03"))


if __name__ == "__main__":
    unittest.main()
