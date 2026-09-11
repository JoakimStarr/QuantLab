"""日志覆盖补全 + 质量快修单测（无需 DB）：

- 审计事件触达：因子创建/删除、策略归档、设置保存（只记键名）、EOD 触发、日志级别变更
- /logs/frontend 前端错误上报：写入 + 字段长度截断 + 级别白名单
- 同步触发限流（slowapi @limiter.limit("2/minute")）
- 日志文件白名单含 eval.log / mining.log
"""
import logging
from unittest.mock import AsyncMock

import pytest
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

import app.api.data_ext as data_ext_module
import app.services.data.sync_worker as sync_worker_module
import app.services.quant.qlib_init as qlib_init_module
from app.api.logs import FrontendLogRequest, _allowed_file, report_frontend_log
from app.core.logging_config import _CLEANUP_PATTERNS
from app.schemas.factor import FactorCreate
from app.schemas.quant import RepairRequest


def _mk_request(ip: str = "10.0.0.1") -> Request:
    """构造带 client IP 的 starlette Request（slowapi 按此 IP 限流计数）。"""
    scope = {"type": "http", "client": (ip, 1234), "headers": [],
             "method": "POST", "path": "/", "query_string": b""}
    return Request(scope)


def _audit_records(caplog):
    return [r for r in caplog.records if r.name == "audit"]


# ---------------- 日志文件白名单 ----------------

def test_allowed_static_contains_worker_logs():
    """eval.log（因子评价 worker）与 mining.log（挖掘 worker）必须 UI 可见。"""
    assert _allowed_file("eval.log")
    assert _allowed_file("mining.log")
    assert not _allowed_file("eval.log.1")
    assert "eval.log.[0-9]*" in _CLEANUP_PATTERNS  # 备份也要纳入清理
    assert "mining.log.[0-9]*" in _CLEANUP_PATTERNS


# ---------------- 审计事件：因子 ----------------

async def test_factor_create_audit(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="audit")
    monkeypatch.setattr("app.api.factor.validate_expression", lambda expr: None)
    monkeypatch.setattr("app.api.factor.add_factor",
                        AsyncMock(return_value={"id": 7, "name": "t"}))
    from app.api.factor import add_factor_api

    res = await add_factor_api(FactorCreate(name="t", expression="$close", category="llm"))
    assert res.ok is True
    recs = [r for r in _audit_records(caplog) if r.__dict__.get("extra_fields", {}).get("action") == "factor_create"]
    assert len(recs) == 1
    fields = recs[0].__dict__["extra_fields"]
    assert fields["resource"] == "factor:7"
    assert fields["user"] == "anonymous"


async def test_factor_delete_audit_warning(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="audit")
    monkeypatch.setattr("app.api.factor.disable_factor", AsyncMock(return_value=True))
    from app.api.factor import disable_factor_api

    res = await disable_factor_api(42)
    assert res.ok is True
    recs = [r for r in _audit_records(caplog) if r.__dict__.get("extra_fields", {}).get("action") == "factor_delete"]
    assert len(recs) == 1
    assert recs[0].levelno == logging.WARNING  # 删除类事件必须 WARNING 级
    assert recs[0].__dict__["extra_fields"]["resource"] == "factor:42"


# ---------------- 审计事件：策略归档 ----------------

async def test_strategy_archive_audit_warning(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="audit")
    monkeypatch.setattr("app.api.strategy.archive_strategy", AsyncMock(return_value=True))
    from app.api.strategy import archive_strategy_api

    res = await archive_strategy_api(9)
    assert res.ok is True
    recs = [r for r in _audit_records(caplog) if r.__dict__.get("extra_fields", {}).get("action") == "strategy_archive"]
    assert len(recs) == 1
    assert recs[0].levelno == logging.WARNING
    assert recs[0].__dict__["extra_fields"]["resource"] == "strategy:9"


# ---------------- 审计事件：设置保存（只记键名，不记值） ----------------

async def test_settings_save_audit_keys_only(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="audit")
    monkeypatch.setattr("app.api.settings._save_config_yaml", lambda updates: None)
    monkeypatch.setattr("app.api.settings._upsert_env_file", lambda path, updates: None)
    from app.api.settings import SaveSettingsRequest, save_settings

    payload = SaveSettingsRequest(quant={"n_candidates": 3}, api_keys={"GLM_API_KEY": "sk-secret-value-123"})
    res = await save_settings(payload)
    assert res.ok is True
    recs = [r for r in _audit_records(caplog) if r.__dict__.get("extra_fields", {}).get("action") == "settings_save"]
    assert len(recs) == 1
    fields = recs[0].__dict__["extra_fields"]
    assert "GLM_API_KEY" in fields["api_key_names"]  # 只记键名
    assert fields["changed_keys"] == {"quant": ["n_candidates"]}
    # 值绝不落日志
    assert "sk-secret-value-123" not in recs[0].getMessage()
    assert "sk-secret-value-123" not in str(fields)


# ---------------- 审计事件：EOD 触发 ----------------

@pytest.fixture
def _patch_sync_triggers(monkeypatch):
    """EOD/sync-full/repair 端点共同的外部依赖：qlib 可用 + 进度空闲 + spawn 记录。"""
    spawned = []
    monkeypatch.setattr(qlib_init_module, "is_qlib_available", AsyncMock(return_value=True))
    monkeypatch.setattr(data_ext_module, "ensure_no_bin_sync", lambda *a, **k: None)
    monkeypatch.setattr(sync_worker_module, "spawn_sync_worker",
                        lambda kind, universe, **kw: spawned.append((kind, universe, kw)))
    return spawned


async def test_eod_sync_audit(caplog, _patch_sync_triggers):
    caplog.set_level(logging.INFO, logger="audit")
    from app.api.data_ext import eod_sync_api

    res = await eod_sync_api(_mk_request("10.0.0.2"), universe="all", days=5,
                             overwrite=False, source="baostock")
    assert res.ok is True
    recs = [r for r in _audit_records(caplog) if r.__dict__.get("extra_fields", {}).get("action") == "eod_sync_submit"]
    assert len(recs) == 1
    fields = recs[0].__dict__["extra_fields"]
    assert fields["universe"] == "all"
    assert fields["days"] == 5
    assert fields["source"] == "baostock"


# ---------------- 同步触发限流 ----------------

async def test_sync_endpoints_rate_limited(_patch_sync_triggers):
    """超过 2 次/分钟后第 3 次触发 RateLimitExceeded（独立 IP 避免测试间串扰）。"""
    from app.api.data_ext import eod_sync_api

    for _ in range(2):
        res = await eod_sync_api(_mk_request("10.9.9.9"), universe="all", days=5,
                                 overwrite=False, source="baostock")
        assert res.ok is True
    with pytest.raises(RateLimitExceeded):
        await eod_sync_api(_mk_request("10.9.9.9"), universe="all", days=5,
                           overwrite=False, source="baostock")


async def test_repair_endpoint_rate_limited():
    """repair 端点同样限流（不触 DB：第 3 次在进入函数体前被拦）。"""
    from app.api.data_ext import repair_api

    for _ in range(2):
        # 前两次会进入函数体（qlib 检查 + DB），用被 fixture 拦截的环境：
        # 这里只验证第 3 次被限流器拦截，因此前两次用 try 吞掉函数体内部错误。
        try:
            await repair_api(_mk_request("10.8.8.8"), req=RepairRequest(universe="csi300"),
                             db=None)
        except RateLimitExceeded:
            pytest.fail("前两次调用不应被限流")
        except Exception:
            pass  # 函数体内 DB/qlib 依赖在单测环境不可用，允许失败
    with pytest.raises(RateLimitExceeded):
        await repair_api(_mk_request("10.8.8.8"), req=RepairRequest(universe="csi300"), db=None)


# ---------------- 日志级别变更审计 ----------------

def test_set_log_level_audit(caplog):
    from app.core.logging_config import _MANAGED_LOGGERS, set_log_level

    try:
        caplog.set_level(logging.INFO, logger="audit")
        set_log_level("DEBUG")
        recs = [r for r in _audit_records(caplog) if r.__dict__.get("extra_fields", {}).get("action") == "log_level_change"]
        assert len(recs) == 1
        assert recs[0].__dict__["extra_fields"]["level"] == "DEBUG"
    finally:
        # 复位，避免污染其它测试
        for name in ("", *_MANAGED_LOGGERS):
            logging.getLogger(name).setLevel(logging.INFO)


# ---------------- /logs/frontend 前端错误上报 ----------------

async def test_frontend_log_report_and_truncation(caplog):
    caplog.set_level(logging.ERROR, logger="frontend")
    res = await report_frontend_log(FrontendLogRequest(
        message="M" * 1000, stack="S" * 10000, route="R" * 1000, level="error"))
    assert res.ok is True
    assert res.data["logged"] is True
    recs = [r for r in caplog.records if r.name == "frontend"]
    assert recs, "前端错误应写入 frontend logger"
    rec = recs[-1]
    fields = rec.__dict__["extra_fields"]
    assert len(fields["detail"]) <= 500   # message 截断
    assert len(fields["route"]) <= 200    # route 截断
    assert fields["action"] == "frontend_error"


async def test_frontend_log_level_whitelist(caplog):
    """非白名单级别归一化为 error；warning 级别保持 WARNING。"""
    caplog.set_level(logging.WARNING, logger="frontend")
    res = await report_frontend_log(FrontendLogRequest(message="x", level="info"))
    assert res.ok is True
    assert res.data["level"] == "error"  # 归一化
    res2 = await report_frontend_log(FrontendLogRequest(message="y", level="warning"))
    assert res2.data["level"] == "warning"
    warning_recs = [r for r in caplog.records if r.name == "frontend" and r.levelno == logging.WARNING]
    assert warning_recs
