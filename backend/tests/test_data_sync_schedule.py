"""定时数据管理同步（data_sync_schedule_service）测试：配置读写、校验、调度触发逻辑。"""
from datetime import date, datetime

import pytest

from app.services.task import data_sync_schedule_service as svc
from app.services.task.data_sync_schedule_service import (
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
        assert cfg["include_full"] is True
        assert cfg["include_eod"] is False

    async def test_save_and_read_roundtrip(self, db_ready):
        """保存后读回一致。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        saved = await svc.save_schedule({
            "enabled": True,
            "run_time": "18:30",
            "workdays_only": False,
            "include_full": False,
            "include_eod": True,
            "include_indices": True,
            "include_etf": False,
            "include_fundamental": True,
            "years": 3,
            "universe": "csi300",
            "eod_days": 10,
            "etf_days": 60,
        })
        assert saved["enabled"] is True
        assert saved["run_time"] == "18:30"
        assert saved["include_full"] is False
        assert saved["include_eod"] is True
        assert saved["universe"] == "csi300"
        assert saved["eod_days"] == 10

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
        assert cfg["include_full"] is True

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
        await svc.tick_scheduled_data_sync()
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
        await svc.tick_scheduled_data_sync()
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
        await svc.tick_scheduled_data_sync()
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
        await svc.tick_scheduled_data_sync()
        assert len(spawned) == 1
        cfg = await svc.get_schedule()
        assert cfg["last_run_date"] is not None

        # 同一天第二次 tick 不再触发
        await svc.tick_scheduled_data_sync()
        assert len(spawned) == 1

    async def test_run_workers_spawns_selected(self, monkeypatch):
        """_run_workers 按勾选环节 spawn 对应 worker kind。"""
        calls = []

        def fake_spawn(kind, universe, **kwargs):
            calls.append((kind, universe, kwargs))

        # _run_workers 内部 `from app.services.data.sync_worker import spawn_sync_worker`
        monkeypatch.setattr("app.services.data.sync_worker.spawn_sync_worker", fake_spawn)
        await svc._run_workers({
            "include_full": True, "include_eod": True, "include_indices": True,
            "include_etf": True, "include_fundamental": True,
            "years": 3, "universe": "all", "eod_days": 5, "etf_days": 30,
        })
        kinds = [c[0] for c in calls]
        assert kinds == ["full", "eod", "indices", "etf", "fundamental"]
        assert calls[0][2]["years"] == 3
