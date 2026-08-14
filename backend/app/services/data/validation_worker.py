"""数据校验独立 worker（子进程运行，与 web 进程解耦）。

全市场数据校验（run_validation）扫描 5400+ 标的的 bin 完整性、day.txt 对齐、
DB↔bin 覆盖、宏观/财报抽样，耗时数十秒到分钟。原先在 web 进程的请求处理器里
await 执行，前端"数据校验"按钮会一直转圈、阻塞请求。

独立子进程 + start_new_session=True 后：
- 校验不占 web 进程事件循环/线程池
- uvicorn --reload 重启不会等它
- 前端提交后轮询状态文件直到 done，再读报告

状态文件（web 进程读取）：
- data/validation_status.json   {status: running/done/failed, started_at, finished_at, error}
- data/validation_report.json   run_validation 的完整报告
存活标记：data/validation.pid（防重复提交，单实例）

用法（web 进程通过 spawn_validation_worker 调用）:
    python -m app.services.data.validation_worker --universe all
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

PID_DIR = None        # 懒初始化：data/
STATUS_FILE = None    # data/validation_status.json
REPORT_FILE = None    # data/validation_report.json


def _paths() -> tuple[str, str, str]:
    global PID_DIR, STATUS_FILE, REPORT_FILE
    if PID_DIR is None:
        from app.core.config import settings
        data_dir = settings.PROJECT_ROOT / "data"
        PID_DIR = str(data_dir)
        STATUS_FILE = str(data_dir / "validation_status.json")
        REPORT_FILE = str(data_dir / "validation_report.json")
    return PID_DIR, STATUS_FILE, REPORT_FILE


def _pid_path() -> str:
    return os.path.join(_pid_path_dir(), "validation.pid")


def _pid_path_dir() -> str:
    return _paths()[0]


def write_worker_pid() -> None:
    try:
        with open(_pid_path(), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        logger.exception("写入校验 worker pid 失败")


def clear_worker_pid() -> None:
    try:
        p = _pid_path()
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        logger.exception("清理校验 worker pid 失败")


def is_validation_running() -> bool:
    """是否正在校验（有存活的 worker 子进程）。"""
    p = _pid_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        clear_worker_pid()
        return False
    except Exception:
        return False


def write_status(status: dict) -> None:
    """写状态文件（原子替换）。"""
    _, status_file, _ = _paths()
    try:
        tmp = status_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
        os.replace(tmp, status_file)
    except Exception:
        logger.exception("写校验状态失败")


def read_status() -> dict:
    """读状态文件；无文件返回 idle。"""
    _, status_file, _ = _paths()
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"status": "idle"}
    except Exception:
        return {"status": "idle", "error": "状态文件损坏"}


def read_report() -> dict | None:
    _, _, report_file = _paths()
    try:
        with open(report_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def spawn_validation_worker(universe: str = "all") -> subprocess.Popen:
    """启动独立校验 worker 子进程并立即返回。"""
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    cmd = [
        sys.executable, "-m", "app.services.data.validation_worker",
        "--universe", universe,
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", backend_dir)
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=backend_dir,
        start_new_session=True,
        env=env,
    )
    logger.info("validation_worker 已启动 pid=%s universe=%s", proc.pid, universe)

    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("validation_worker 退出 pid=%s code=%s", process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


async def _run(args: argparse.Namespace) -> None:
    from app.core.logging_config import set_worker_kind

    set_worker_kind("validate")
    write_worker_pid()
    started_at = datetime.now().isoformat()
    write_status({
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "error": None,
    })
    try:
        from app.core.config import settings
        from app.services.data.validation import run_validation

        report = await run_validation(
            provider_uri=settings.qlib_provider_path,
            universe=args.universe,
        )
        _, _, report_file = _paths()
        try:
            tmp = report_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False)
            os.replace(tmp, report_file)
        except Exception:
            logger.exception("写校验报告失败")
            raise
        write_status({
            "status": "done",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "error": None,
        })
        logger.info("数据校验完成 universe=%s", args.universe)
    except Exception as e:
        logger.exception("数据校验失败")
        write_status({
            "status": "failed",
            "finished_at": datetime.now().isoformat(),
            "error": str(e)[:500],
        })
        sys.exit(1)
    finally:
        clear_worker_pid()


def main() -> None:
    from app.core.config import settings

    parser = argparse.ArgumentParser(description="QuantLab 数据校验独立 worker")
    parser.add_argument("--universe", default="all")
    args = parser.parse_args()

    from app.core.logging_config import setup_logging

    setup_logging(
        log_dir=settings.PROJECT_ROOT / settings.logging.dir,
        level=settings.logging.level,
        console=False,
        log_file="sync.log",
        error_file=None,
    )

    try:
        import asyncio
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        logging.getLogger(__name__).exception("validation_worker 异常退出")
        sys.exit(1)


if __name__ == "__main__":
    main()
