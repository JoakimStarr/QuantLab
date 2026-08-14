"""定时数据刷新（sync_schedule_service）测试：配置读写、校验、调度触发逻辑。"""
from datetime import date, datetime

import pytest

from app.services.task import sync_schedule_service as svc
from app.services.task.sync_schedule_service import (
    _is_workday,
    _validate_run_time,
)


class TestValidateRunTime:
    def test_valid(self):
        assert _validate_run_time("18:05") == "18:05"
        assert _validate_run_time("09:00:00") == "09:00"
        assert _validate_run_time("7:5") == "07:05"

    @pytest.mark.parametrize("bad", ["", "abc", "25:00", "12:99", "18"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            _validate_run_time(bad)


class TestIsWorkday:
    @pytest.mark.parametrize("d", [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 14)])  # 周一~周五
    def test_weekday(self, d):
        assert _is_workday(d)

    @pytest.mark.parametrize("d", [date(2026, 8, 15), date(2026, 8, 16)])  # 周六、周日
    def test_weekend(self, d):
        assert not _is_workday(d)


class TestSchedulePersistence:
    async def test_defaults_when_empty(self, db_ready):
        """无记录时返回默认配置（enabled=False，不落库）。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        cfg = await svc.get_schedule()
        assert cfg["enabled"] is False
        assert ":" in cfg["run_time"]

    async def test_save_and_read_roundtrip(self, db_ready):
        """保存后读回一致。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        saved = await svc.save_schedule({
            "enabled": True,
            "run_time": "18:30",
            "workdays_only": False,
            "include_news": True,
            "include_ai": False,
            "include_market": True,
            "ai_backfill_days": 7,
            "market_days": 3,
            "market_universe": "all",
        })
        assert saved["enabled"] is True
        assert saved["run_time"] == "18:30"
        assert saved["include_ai"] is False

        cfg = await svc.get_schedule()
        assert cfg == saved

    async def test_save_partial_keeps_others(self, db_ready):
        """部分更新只改传入字段，其余保持默认。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        await svc.save_schedule({"enabled": True})
        cfg = await svc.get_schedule()
        assert cfg["enabled"] is True
        assert cfg["workdays_only"] is True
        assert cfg["include_news"] is True

    async def test_bad_run_time_rejected(self, db_ready):
        """非法时间格式抛 ValueError，不落库。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        with pytest.raises(ValueError):
            await svc.save_schedule({"run_time": "25:99"})


class TestTickLogic:
    async def test_disabled_no_spawn(self, db_ready, monkeypatch):
        """未启用时不 spawn 任何 worker。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        await svc.save_schedule({"enabled": False, "run_time": "00:00"})
        spawned = []

        async def fake_run(cfg):
            spawned.append(cfg)

        monkeypatch.setattr(svc, "_run_workers", fake_run)
        await svc.tick_scheduled_sync()
        assert spawned == []

    async def test_before_run_time_no_spawn(self, db_ready, monkeypatch):
        """未到设定时间不触发。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        await svc.save_schedule({"enabled": True, "run_time": "23:59"})
        spawned = []

        async def fake_run(cfg):
            spawned.append(cfg)

        monkeypatch.setattr(svc, "_run_workers", fake_run)
        await svc.tick_scheduled_sync()
        assert spawned == []

    async def test_weekend_skipped_when_workdays_only(self, db_ready, monkeypatch):
        """workdays_only 且周六时不触发。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        await svc.save_schedule({"enabled": True, "run_time": "00:00", "workdays_only": True})
        spawned = []

        async def fake_run(cfg):
            spawned.append(cfg)

        class _Now(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 8, 15, 9, 0)  # 周六

        monkeypatch.setattr(svc, "_run_workers", fake_run)
        monkeypatch.setattr(svc, "datetime", _Now)
        await svc.tick_scheduled_sync()
        assert spawned == []

    async def test_run_marks_last_run(self, db_ready, monkeypatch):
        """触发后写入 last_run_date，同日再次 tick 不重复触发。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        await svc.save_schedule({"enabled": True, "run_time": "00:00", "workdays_only": False})
        spawned = []

        async def fake_run(cfg):
            spawned.append(cfg)

        monkeypatch.setattr(svc, "_run_workers", fake_run)
        await svc.tick_scheduled_sync()
        assert len(spawned) == 1
        cfg = await svc.get_schedule()
        assert cfg["last_run_date"] is not None

        # 同一天第二次 tick 不再触发
        await svc.tick_scheduled_sync()
        assert len(spawned) == 1
