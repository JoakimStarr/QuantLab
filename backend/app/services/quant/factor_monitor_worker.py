"""因子衰减检测独立 worker（子进程运行，与 web 进程解耦）。

定时任务（每工作日 18:05）原先在 web 进程的 APScheduler 里跑 detect_all_factors_decay，
遍历全部 active 因子逐个 load 5 年数据算 IC 衰减，因子多时会长时间占 web 进程
线程池（与请求抢 io_workers 额度），也可能撞上 reload 关停。

独立子进程 + start_new_session=True 后：不占 web 进程资源，reload 不影响。

结果写 data/decay_check.json 存档（无前端实时消费，WebSocket 告警已无使用方，
仅保留日志与结果文件记录）。

用法（scheduler 通过 spawn_decay_check_worker 调用）:
    python -m app.services.quant.factor_monitor_worker
"""
import json
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

PID_FILE = None   # data/decay_check.pid
RESULT_FILE = None  # data/decay_check.json


def _paths() -> tuple[str, str]:
    global PID_FILE, RESULT_FILE
    if PID_FILE is None:
        from app.core.config import settings
        data_dir = settings.PROJECT_ROOT / "data"
        PID_FILE = str(data_dir / "decay_check.pid")
        RESULT_FILE = str(data_dir / "decay_check.json")
    return PID_FILE, RESULT_FILE


def write_worker_pid() -> None:
    try:
        with open(_paths()[0], "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        logger.exception("写入衰减检测 pid 失败")


def clear_worker_pid() -> None:
    try:
        p = _paths()[0]
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        logger.exception("清理衰减检测 pid 失败")


def is_decay_check_running() -> bool:
    """是否正在做衰减检测（防止定时任务重复触发）。"""
    p = _paths()[0]
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


def spawn_decay_check_worker() -> subprocess.Popen:
    """启动独立衰减检测 worker 子进程并立即返回。"""
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    cmd = [sys.executable, "-m", "app.services.quant.factor_monitor_worker"]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", backend_dir)
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=backend_dir,
        start_new_session=True,
        env=env,
    )
    logger.info("factor_monitor_worker 已启动 pid=%s", proc.pid)

    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("factor_monitor_worker 退出 pid=%s code=%s", process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


async def _run() -> None:
    from app.core.logging_config import set_worker_kind

    set_worker_kind("decay_check")
    write_worker_pid()
    try:
        from app.core.database import async_session
        from app.services.quant.factor_monitor import detect_all_factors_decay

        async with async_session() as session:
            result = await detect_all_factors_decay(db_session=session)

        _, result_file = _paths()
        try:
            tmp = result_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, default=str)
            os.replace(tmp, result_file)
        except Exception:
            logger.exception("写衰减检测结果失败")

        logger.info("因子衰减检测完成: %d 个因子, %d 个衰减",
                    result.get("total", 0), result.get("decaying", 0))
    except Exception as e:
        logger.exception("因子衰减检测失败: %s", str(e)[:300])
        sys.exit(1)
    finally:
        clear_worker_pid()


def main() -> None:
    from app.core.config import settings

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
        asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        logging.getLogger(__name__).exception("factor_monitor_worker 异常退出")
        sys.exit(1)


if __name__ == "__main__":
    main()
