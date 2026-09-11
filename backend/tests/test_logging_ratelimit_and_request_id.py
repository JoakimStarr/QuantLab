"""日志限速过滤器（RateLimitFilter）与 worker request_id 贯通的测试。

覆盖：
- RateLimitFilter：首条放行 / 重复抑制 / 不同消息互不影响 / 窗口结束摘要 /
  窗口重置 / ERROR+WARNING 同样限速 / audit 豁免 / 线程安全（并发抑制不漏首条）
- request_id 从 spawn 到 worker argv 的透传（mock subprocess.Popen）
- set_request_id / get_request_id 的 contextvar 行为
"""
import logging
import threading
import time
from contextvars import copy_context
from unittest.mock import patch

from app.core.logging_config import (
    RateLimitFilter,
    get_request_id,
    request_id_var,
    set_request_id,
)


class _Capture(logging.Handler):
    """捕获记录的 handler（配合 RateLimitFilter 测试）。"""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _make_logger(name: str, rate_filter: RateLimitFilter) -> tuple[logging.Logger, _Capture]:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    handler.addFilter(rate_filter)
    logger.addHandler(handler)
    return logger, handler


def _cleanup_logger(name: str) -> None:
    lg = logging.getLogger(name)
    for h in list(lg.handlers):
        lg.removeHandler(h)
    lg.propagate = True


# ---------------- RateLimitFilter 基础行为 ----------------

def test_ratelimit_first_record_passes_duplicates_suppressed():
    f = RateLimitFilter(window=60.0)
    logger, handler = _make_logger("rl-test-basic", f)
    try:
        logger.warning("同步失败 universe=%s", "csi300")
        logger.warning("同步失败 universe=%s", "csi300")
        logger.warning("同步失败 universe=%s", "csi300")
        # 同模板同级别：只放行首条
        assert len(handler.records) == 1
        # 不同参数的日志仍以"模板"为 key：同模板已抑制 → 依旧不放行
        logger.warning("同步失败 universe=%s", "csi500")
        assert len(handler.records) == 1
    finally:
        _cleanup_logger("rl-test-basic")


def test_ratelimit_different_templates_and_levels_pass():
    f = RateLimitFilter(window=60.0)
    logger, handler = _make_logger("rl-test-diff", f)
    try:
        logger.warning("模板A %s", 1)
        logger.warning("模板B %s", 1)
        logger.error("模板A %s", 1)
        logger.info("模板A %s", 1)
        # 4 条互不相同（模板/级别任一不同）→ 全部放行
        assert len(handler.records) == 4
        # 同 key 重复 → 抑制
        logger.warning("模板A %s", 2)
        assert len(handler.records) == 4
    finally:
        _cleanup_logger("rl-test-diff")


def test_ratelimit_summary_emitted_at_window_end():
    f = RateLimitFilter(window=0.05)
    logger, handler = _make_logger("rl-test-window", f)
    try:
        logger.error("风暴消息 key=%s", 1)
        for _ in range(5):
            logger.error("风暴消息 key=%s", 1)
        assert len(handler.records) == 1  # 首条可见，5 条被抑制
        # 等待窗口结束 Timer 发摘要
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(handler.records) < 2:
            time.sleep(0.02)
        assert len(handler.records) == 2, "窗口结束应追加一条抑制摘要"
        summary = handler.records[1].getMessage()
        assert "suppressed 5 duplicates" in summary
        assert "风暴消息 key=%s" in summary
        assert handler.records[1].levelno == logging.ERROR
    finally:
        _cleanup_logger("rl-test-window")


def test_ratelimit_window_reset_after_expiry():
    f = RateLimitFilter(window=0.05)
    logger, handler = _make_logger("rl-test-reset", f)
    try:
        logger.warning("周期消息", )
        logger.warning("周期消息")
        assert len(handler.records) == 1
        # 等窗口结束（Timer 发摘要 + 状态清空）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(handler.records) < 2:
            time.sleep(0.02)
        assert len(handler.records) == 2
        # 新窗口：同模板再次放行首条
        logger.warning("周期消息")
        assert len(handler.records) == 3
    finally:
        _cleanup_logger("rl-test-reset")


def test_ratelimit_audit_exempt():
    # audit 事件以 logger 名 "audit" 走统一管道 → 过滤器按 record.name 豁免
    f = RateLimitFilter(window=60.0)
    for _ in range(10):
        record = logging.LogRecord("audit", logging.WARNING, "p", 1,
                                   "审计事件 action=%s", ("login",), None)
        assert f.filter(record) is True


def test_ratelimit_audit_exempt_regardless_of_logger_name_suffix():
    # 直接用 filter 过滤 audit logger 的 record（模拟 root handler 路径）
    f = RateLimitFilter(window=60.0)
    record = logging.LogRecord("audit", logging.INFO, "p", 1,
                               "重复审计 %s", ("x",), None)
    assert f.filter(record) is True
    record2 = logging.LogRecord("audit", logging.INFO, "p", 1,
                                "重复审计 %s", ("x",), None)
    assert f.filter(record2) is True


def test_ratelimit_thread_safety_first_and_summary_only():
    f = RateLimitFilter(window=60.0)
    logger, handler = _make_logger("rl-test-threads", f)
    try:
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            logger.error("并发风暴模板")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 首条只放行一次，其余 7 条抑制
        assert len(handler.records) == 1
    finally:
        _cleanup_logger("rl-test-threads")


def test_ratelimit_structlog_style_dict_msg_key():
    # structlog 走 stdlib 时 record.msg 是事件字典（不可哈希）→ 不应抛异常
    f = RateLimitFilter(window=60.0)
    logger, handler = _make_logger("rl-test-dict", f)
    try:
        logger.warning({"event": "字典消息", "k": 1})
        logger.warning({"event": "字典消息", "k": 1})
        assert len(handler.records) == 1
        logger.warning({"event": "字典消息", "k": 2})
        # 同一事件字典 repr 相同 → 抑制
        assert len(handler.records) == 1
    finally:
        _cleanup_logger("rl-test-dict")


# ---------------- request_id：spawn → argv → worker contextvar ----------------

def _popen_args(mock_popen) -> list:
    call = mock_popen.call_args
    if call.kwargs.get("args"):
        return call.kwargs["args"]
    return call[0][0]


def test_spawn_sync_worker_passes_request_id():
    from app.services.data import sync_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_sync_worker("eod", "all", days=5, request_id="req-abc-1")
    cmd = _popen_args(mock_popen)
    assert "--request-id" in cmd
    assert cmd[cmd.index("--request-id") + 1] == "req-abc-1"


def test_spawn_sync_worker_omits_request_id_when_absent():
    from app.services.data import sync_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_sync_worker("eod", "all", days=5)
    cmd = _popen_args(mock_popen)
    assert "--request-id" not in cmd


def test_spawn_mining_worker_passes_request_id():
    from app.services.mining import mining_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_mining_worker(42, "llm", {"universe": "csi300"}, request_id="req-m-7")
    cmd = _popen_args(mock_popen)
    assert "--request-id" in cmd
    assert cmd[cmd.index("--request-id") + 1] == "req-m-7"


def test_spawn_strategy_worker_passes_request_id():
    from app.services.strategy import strategy_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_strategy_worker("backtest", 7, {"backend": "qlib"}, request_id="req-s-3")
    cmd = _popen_args(mock_popen)
    assert "--request-id" in cmd
    assert cmd[cmd.index("--request-id") + 1] == "req-s-3"


def test_spawn_validation_worker_passes_request_id():
    from app.services.data import validation_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_validation_worker("all", request_id="req-v-9")
    cmd = _popen_args(mock_popen)
    assert "--request-id" in cmd
    assert cmd[cmd.index("--request-id") + 1] == "req-v-9"


def test_spawn_factor_eval_worker_passes_request_id():
    from app.services.factor import factor_eval_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_factor_eval_worker(11, request_id="req-f-2")
    cmd = _popen_args(mock_popen)
    assert "--request-id" in cmd
    assert cmd[cmd.index("--request-id") + 1] == "req-f-2"


def test_spawn_decay_check_worker_accepts_request_id():
    from app.services.quant import factor_monitor_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_decay_check_worker(request_id="req-d-4")
    cmd = _popen_args(mock_popen)
    assert "--request-id" in cmd
    assert cmd[cmd.index("--request-id") + 1] == "req-d-4"


def test_set_request_id_sets_both_contextvars():
    seen = {}

    def probe():
        from asgi_correlation_id import correlation_id as cid
        seen["rid"] = request_id_var.get("")
        seen["cid"] = cid.get()

    # 在独立 context 中 set + 读，避免污染其他测试的上下文
    ctx = copy_context()
    ctx.run(lambda: (set_request_id("req-worker-77"), probe()))
    assert seen["rid"] == "req-worker-77"
    assert seen["cid"] == "req-worker-77"


def test_set_request_id_none_is_noop():
    ctx = copy_context()

    def run_in_ctx():
        request_id_var.set("")  # 起点：空
        set_request_id(None)
        assert request_id_var.get("") == ""

    ctx.run(run_in_ctx)


def test_get_request_id_prefers_request_id_var():
    ctx = copy_context()

    def run_in_ctx():
        token = request_id_var.set("req-from-var")
        try:
            assert get_request_id() == "req-from-var"
        finally:
            request_id_var.reset(token)
        # 无上下文时返回 None（或包内空值 → None）
        rid = get_request_id()
        assert rid in (None, "")

    ctx.run(run_in_ctx)
