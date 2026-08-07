"""统一日志配置单测（子进程隔离，避免污染全局 logging 状态）。

验证：
- setup_logging 参数化：log_file/error_file 分别生成 quantlab.log+error.log 或仅 sync.log
- 同步 worker 配置产出的 JSON 行含 worker_kind/pid
- 第三方 logger（uvicorn/asgi_correlation_id/apscheduler）级别被显式管理
- 动态调级 set_log_level 覆盖 root + 受管 logger
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


def _run_code(code: str, log_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", code, str(log_dir)],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT), env=env,
    )


def test_setup_logging_worker_variant(tmp_path):
    """worker 配置：只产出 sync.log，JSON 行含 worker_kind/pid；受管 logger 级别正确。"""
    code = """
import json, sys, logging, os
from pathlib import Path
from app.core.logging_config import (
    setup_logging, set_worker_kind, set_log_level,
)

log_dir = Path(sys.argv[1])
setup_logging(log_dir=log_dir, level="INFO", console=False,
              log_file="sync.log", error_file=None)

set_worker_kind("backfill")
logging.getLogger("test_worker").info("sync worker started")

# 受管 logger 级别（消噪配置）
assert logging.getLogger("uvicorn.access").level == logging.WARNING
assert logging.getLogger("asgi_correlation_id").level == logging.ERROR
assert logging.getLogger("apscheduler").level == logging.WARNING

# 动态调级覆盖 root + 受管 logger
set_log_level("DEBUG")
assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
assert logging.getLogger("uvicorn").level == logging.DEBUG
set_log_level("INFO")

assert (log_dir / "sync.log").exists()
assert not (log_dir / "error.log").exists()

lines = [l for l in (log_dir / "sync.log").read_text(encoding="utf-8").splitlines() if l.strip()]
entry = next(json.loads(l) for l in lines if '"worker_kind"' in l)
assert entry["worker_kind"] == "backfill", entry
assert entry["pid"] == os.getpid(), entry
assert entry["level"] == "info", entry
print("OK")
"""
    proc = _run_code(code, tmp_path / "logs")
    assert proc.returncode == 0, "子进程失败: " + proc.stderr[-2000:]
    assert "OK" in proc.stdout


def test_setup_logging_web_defaults(tmp_path):
    """默认 web 配置产出 quantlab.log + error.log 双文件，WARNING 进 error.log。"""
    code = """
import sys, logging
from app.core.logging_config import setup_logging

log_dir = sys.argv[1]
setup_logging(log_dir=log_dir, level="INFO", console=False)
logging.getLogger("test_web").warning("warn line")
print("DONE")
"""
    proc = _run_code(code, tmp_path / "logs")
    assert proc.returncode == 0, "子进程失败: " + proc.stderr[-2000:]
    d = tmp_path / "logs"
    assert (d / "quantlab.log").exists()
    assert (d / "error.log").exists()
    assert "warn line" in (d / "error.log").read_text(encoding="utf-8")
    assert "warn line" in (d / "quantlab.log").read_text(encoding="utf-8")
