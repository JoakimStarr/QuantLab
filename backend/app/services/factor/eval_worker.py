"""因子评价独立 worker（子进程运行，与 web 进程解耦）。

为什么独立子进程：
- 因子评价（evaluate_factor_by_id → run_cpu 进程池）是 CPU 密集长任务。
  若跑在 web 进程内，文件变更触发 uvicorn --reload 时，解释器退出会 join
  进程池等待在途任务结束——长评价会把 reload 卡死、整个服务失去响应。
- 独立子进程 + start_new_session=True 后：web 进程重启/崩溃不影响评价，
  uvicorn --reload 也不会等待它退出（与 sync_worker/mining_worker 同款设计）。

用法（web 进程通过 spawn_factor_eval_worker 调用）:
    python -m app.services.factor.eval_worker --factor-id 42 \
        --start 2020-01-01 --end 2024-12-31 --universe csi300
"""
import argparse
import asyncio
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

# 评价 worker 存活标记目录：web 进程据此判断某因子是否正在评价，
# 用于防重复提交；web 重启不会误判（进程不存在则清理陈旧 pid 文件）。
PID_DIR = None  # 懒初始化：settings.PROJECT_ROOT / "data" / "factor_eval_pids"


def _pid_dir() -> str:
    global PID_DIR
    if PID_DIR is None:
        from app.core.config import settings
        PID_DIR = str(settings.PROJECT_ROOT / "data" / "factor_eval_pids")
    return PID_DIR


def _pid_path(factor_id: int) -> str:
    return os.path.join(_pid_dir(), f"{factor_id}.pid")


def write_worker_pid(factor_id: int) -> None:
    """记录当前 worker PID，供 web 进程判断存活。"""
    try:
        os.makedirs(_pid_dir(), exist_ok=True)
        with open(_pid_path(factor_id), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        logger.exception("写入评价 worker pid 失败 factor_id=%s", factor_id)


def clear_worker_pid(factor_id: int) -> None:
    """删除 PID 标记（worker 退出时调用，幂等）。"""
    try:
        p = _pid_path(factor_id)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        logger.exception("清理评价 worker pid 失败 factor_id=%s", factor_id)


def is_factor_eval_running(factor_id: int) -> bool:
    """该因子是否正在评价（有存活的评价 worker 子进程）。"""
    p = _pid_path(factor_id)
    try:
        with open(p, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # 进程存在则返回，否则抛 ProcessLookupError
        return True
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        clear_worker_pid(factor_id)
        return False
    except Exception:
        return False


def spawn_factor_eval_worker(factor_id: int, start: str = None, end: str = None,
                             universe: str = None) -> subprocess.Popen:
    """启动独立评价 worker 子进程并立即返回。

    start_new_session=True 使 worker 脱离 web 进程的进程组：
    - uvicorn --reload 重启时不会等待/杀掉它
    - web 进程崩溃也不影响它继续跑
    日志写入 logs/sync.log（worker_kind=factor_eval 区分）。
    """
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    cmd = [
        sys.executable, "-m", "app.services.factor.eval_worker",
        "--factor-id", str(factor_id),
    ]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    if universe:
        cmd += ["--universe", universe]

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", backend_dir)
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=backend_dir,
        start_new_session=True,
        env=env,
    )
    logger.info("factor_eval_worker 已启动 factor_id=%s pid=%s",
                factor_id, proc.pid)

    # 回收线程（reaper）：worker 退出后无人 waitpid 会残留僵尸进程
    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("factor_eval_worker 退出 factor_id=%s pid=%s code=%s",
                        factor_id, process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


async def _run(args: argparse.Namespace) -> None:
    # 注入 worker_kind 上下文，日志 JSON 行内携带该字段
    from app.core.logging_config import set_worker_kind

    set_worker_kind("factor_eval")
    write_worker_pid(args.factor_id)
    try:
        from app.services.factor.library import evaluate_factor_by_id
        await evaluate_factor_by_id(args.factor_id, args.start, args.end, args.universe)
        logger.info("因子评价完成 factor_id=%s (start=%s end=%s universe=%s)",
                    args.factor_id, args.start, args.end, args.universe)
    except Exception as e:
        logger.exception("因子评价失败 factor_id=%s: %s", args.factor_id, str(e)[:300])
        sys.exit(1)
    finally:
        clear_worker_pid(args.factor_id)


def main() -> None:
    from app.core.config import settings

    parser = argparse.ArgumentParser(description="QuantLab 因子评价独立 worker")
    parser.add_argument("--factor-id", type=int, required=True)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--universe", default=None)
    args = parser.parse_args()

    # 统一日志：worker 写 sync.log，跨进程写入/轮转由 LockedRotatingFileHandler 保证安全
    from app.core.logging_config import setup_logging

    setup_logging(
        log_dir=settings.PROJECT_ROOT / settings.logging.dir,
        level=settings.logging.level,
        console=False,
        log_file="sync.log",
        error_file=None,
    )

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        logging.getLogger(__name__).exception("factor_eval_worker 异常退出")
        sys.exit(1)


if __name__ == "__main__":
    main()
