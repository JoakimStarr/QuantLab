"""交易日历工具测试：exchange_calendars（XSHG）与降级路径。"""
import pandas as pd
import pytest

from app.services.quant import calendar_utils as cu


@pytest.fixture(autouse=True)
def _clean_calendar():
    """每测结束后重置日历单例，避免跨测试污染。"""
    yield
    cu._calendar = None
    cu._calendar_available = False


class TestTradingDays:
    def test_get_trading_days_range(self):
        days = cu.get_trading_days("2024-01-01", "2024-01-31")
        assert isinstance(days, pd.DatetimeIndex)
        assert len(days) > 0
        # 周末不应出现
        assert not any(d.dayofweek >= 5 for d in days)
        # 首尾闭区间
        assert days.min() >= pd.Timestamp("2024-01-01")
        assert days.max() <= pd.Timestamp("2024-01-31")

    def test_get_trading_days_holiday_aware(self):
        """2024-02-09~2024-02-17 春节假期（若日历可用则包含规则）。"""
        days = cu.get_trading_days("2024-02-08", "2024-02-20")
        if cu.is_calendar_available():
            # 春节休市：2/9(五)~2/17(六)，2/19(一) 复市
            assert pd.Timestamp("2024-02-19") in days
            assert pd.Timestamp("2024-02-09") not in days
        else:
            # 降级：工作日近似，周五 2/9 在内
            assert pd.Timestamp("2024-02-09") in days

    def test_is_trading_day(self):
        assert cu.is_trading_day("2024-01-15")  # 周一
        assert not cu.is_trading_day("2024-01-14")  # 周日

    def test_next_prev_trading_day(self):
        # 周五 -> 下周一
        next_day = cu.next_trading_day("2024-01-12")  # 周五
        assert pd.Timestamp(next_day).dayofweek == 0
        # 周一 -> 上周五
        prev_day = cu.prev_trading_day("2024-01-15")  # 周一
        assert pd.Timestamp(prev_day).dayofweek == 4

    def test_offset_trading_days(self):
        base = pd.Timestamp("2024-01-15")  # 周一
        assert cu.offset_trading_days(base, 0) == base
        assert cu.offset_trading_days(base, 5).dayofweek == 0  # +5 交易日回周一
        assert cu.offset_trading_days(base, -5).dayofweek == 0

    def test_align_dates_to_trading_days(self):
        dates = ["2024-01-13", "2024-01-14", "2024-01-15"]  # 六日一
        aligned = cu.align_dates_to_trading_days(dates)
        assert len(aligned) >= 1
        assert all(d.dayofweek < 5 for d in aligned)

    def test_calendar_missing_fallback(self, monkeypatch):
        """exchange_calendars 加载失败时降级为工作日（不抛异常）。"""
        monkeypatch.setattr(cu, "_load_calendar", lambda: None)
        days = cu.get_trading_days("2024-01-01", "2024-01-07")
        assert len(days) == 5  # 周一~周五
