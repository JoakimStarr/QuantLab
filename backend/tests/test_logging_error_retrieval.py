"""错误日志可回溯闭环测试（无需 DB）。

验证链路：错误 + request_id → error.log（含可读 traceback）→ /logs 解析逻辑按
request_id 检索到该错误。用子进程隔离，避免污染当前进程的全局 logging 配置。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

_TEST_CODE = '''
import json, sys, logging
from app.core.logging_config import setup_logging, request_id_var

log_dir = sys.argv[1]
setup_logging(log_dir=log_dir, level="INFO", json_format=True)
request_id_var.set("req-err-abc12345")

logger = logging.getLogger("test_err_loc")
try:
    1 / 0
except ZeroDivisionError:
    logger.error("boom: division failed", exc_info=True)
logger.warning("warn-only line")
print("DONE")
'''


def _run_subprocess(log_dir: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _TEST_CODE, str(log_dir)],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT), env=env,
    )
    assert proc.returncode == 0, "子进程失败: " + proc.stderr[-2000:]
    assert "DONE" in proc.stdout


def test_error_log_locatable_by_request_id(tmp_path):
    log_dir = tmp_path / "logs"
    _run_subprocess(log_dir)

    err_file = log_dir / "error.log"
    assert err_file.exists()

    lines = [l for l in err_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    # error.log 收 WARNING+，应同时含 error 与 warning 两条 JSON
    assert len(lines) >= 2
    entries = [json.loads(l) for l in lines]

    err = next(e for e in entries if e.get("level") == "error")
    assert err["request_id"] == "req-err-abc12345"
    # structlog 用 "event" 作为消息键
    assert "division failed" in err.get("event", "")
    # traceback 必须格式化为可读文本（不能是 <traceback object at ...>）
    assert "ZeroDivisionError" in (err.get("exception") or "")

    # 通过 /logs 的解析逻辑按 request_id 检索到该错误（message 键已映射）
    from app.api.logs import _read_log_file

    parsed = _read_log_file(err_file, is_json=True, max_lines=50)
    hit = [e for e in parsed if e.get("request_id") == "req-err-abc12345" and e.get("level") == "error"]
    assert len(hit) == 1
    assert "division failed" in (hit[0]["message"] or "")
    assert "ZeroDivisionError" in (hit[0]["traceback"] or "")


def test_plain_error_without_exc_info_ok(tmp_path):
    """不带 exc_info 的错误也能正常落盘并检索（不因缺 traceback 抛异常）。"""
    log_dir = tmp_path / "logs"
    _run_subprocess(log_dir)
    err_file = log_dir / "error.log"
    text = err_file.read_text(encoding="utf-8")
    assert "warn-only line" in text
