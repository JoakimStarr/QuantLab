# -*- coding: utf-8 -*-
"""quant/data/status API 单元测试：today_is_trading_day 识别。

覆盖：
- 今日是交易日（日历命中/工作日回退）→ today_is_trading_day=True
- 今日非交易日（周六，日历无记录）→ False
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.quant_data import data_status_api


class _FakeDatetime(datetime):
    """固定 today=2026-08-08（周六）。"""

    _fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def _mock_db(cal_has_today: bool):
    """mock db.execute：count + status 空列表 + calendar 查询。"""
    db = AsyncMock()
    res = MagicMock()
    res.scalar.return_value = 0
    res.scalars.return_value.all.return_value = []
    res.first.return_value = (datetime(2026, 8, 8),) if cal_has_today else None
    db.execute.return_value = res
    return db


async def _call(cal_has_today: bool, fake_now: datetime):
    patch_target = "app.api.quant_data.datetime"
    _FakeDatetime._fixed = fake_now
    with (
        patch(patch_target, _FakeDatetime),
        patch("app.api.quant_data._detect_stale_sync", new=AsyncMock(return_value=0)),
    ):
        return await data_status_api(_mock_db(cal_has_today))


@pytest.mark.asyncio
async def test_status_saturday_not_in_calendar():
    """周六 2026-08-08：日历无今天 → 非交易日 + 工作日回退也不命中。"""
    res = await _call(cal_has_today=False, fake_now=datetime(2026, 8, 8, 10, 0))
    assert res.ok
    assert res.data["today_is_trading_day"] is False
    assert res.data["items"] == []


@pytest.mark.asyncio
async def test_status_weekday_not_in_calendar_fallback_to_weekday():
    """周五 2026-08-07：日历无今天 → 回退工作日推断 → 交易日。"""
    res = await _call(cal_has_today=False, fake_now=datetime(2026, 8, 7, 10, 0))
    assert res.ok
    assert res.data["today_is_trading_day"] is True


@pytest.mark.asyncio
async def test_status_trading_day_in_calendar():
    """交易日 2026-08-07：日历有记录 → 交易日。"""
    res = await _call(cal_has_today=True, fake_now=datetime(2026, 8, 7, 10, 0))
    assert res.ok
    assert res.data["today_is_trading_day"] is True