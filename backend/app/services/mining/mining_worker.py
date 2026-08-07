"""因子挖掘独立 worker（子进程运行，与 web 进程解耦）。

为什么需要独立子进程：
- 挖掘任务（LLM 多轮迭代/符号回归/文本因子/AutoML）可能跑数分钟甚至数小时，
  若用 FastAPI BackgroundTasks 在 web 进程里跑，uvicorn --reload 触发重载时会
  "等待后台任务完成"，导致 reload 卡死、新 worker 起不来。
- 与 sync_worker 同一设计：独立进程组（start_new_session=True），web 进程重启
  不会杀它；任务状态写数据库（mining_task），web 进程通过 /mining/tasks 继续
  向前端提供实时信息。

用法（CLI，web 进程通过 spawn_mining_worker 调用）:
    python -m app.services.mining.mining_worker --task-id 42 --type llm \
        --params '{"n_candidates":10,"n_rounds":1,"universe":"csi300"}'
    python -m app.services.mining.mining_worker --task-id 43 --type symbolic \
        --params '{"universe":"csi300"}'
"""
import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 挖掘 worker 存活标记目录：web 进程据此判断任务是否真的还在跑，
# 避免 web 重启（uvicorn --reload）时把仍存活的子进程任务误标 failed
# （与 sync_progress 的 worker_pid 同款机制）。
PID_DIR = None  # 懒初始化：settings.PROJECT_ROOT / "data" / "mining_pids"


def _pid_dir() -> str:
    global PID_DIR
    if PID_DIR is None:
        from app.core.config import settings
        PID_DIR = str(settings.PROJECT_ROOT / "data" / "mining_pids")
    return PID_DIR


def _pid_path(task_id: int) -> str:
    return os.path.join(_pid_dir(), f"{task_id}.pid")


def write_worker_pid(task_id: int) -> None:
    """记录当前 worker PID，供 web 进程判断存活。"""
    try:
        os.makedirs(_pid_dir(), exist_ok=True)
        with open(_pid_path(task_id), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        logger.exception("写入挖掘 worker pid 失败 task_id=%s", task_id)


def clear_worker_pid(task_id: int) -> None:
    """删除 PID 标记（worker 退出时调用，幂等）。"""
    try:
        p = _pid_path(task_id)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        logger.exception("清理挖掘 worker pid 失败 task_id=%s", task_id)


def is_mining_worker_alive(task_id: int) -> bool:
    """该任务是否有真正存活的挖掘 worker 子进程。"""
    p = _pid_path(task_id)
    try:
        with open(p, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # 进程存在则返回，否则抛 ProcessLookupError
        return True
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        clear_worker_pid(task_id)
        return False
    except Exception:
        return False


def spawn_mining_worker(task_id: int, task_type: str, params: dict) -> subprocess.Popen:
    """启动一个独立的挖掘 worker 子进程并立即返回。

    start_new_session=True 使 worker 脱离 web 进程的进程组：
    - uvicorn --reload 重启时不会等待/杀掉它
    - web 进程崩溃也不影响它继续跑
    日志由 worker 自身写入 logs/mining.log（structlog JSON，worker_kind 区分类型）。
    """
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    log_path = str(settings.PROJECT_ROOT / "logs" / "mining.log")

    cmd = [
        sys.executable, "-m", "app.services.mining.mining_worker",
        "--task-id", str(task_id), "--type", task_type,
        "--params", json.dumps(params, ensure_ascii=False),
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
    logger.info("mining_worker 已启动 task_id=%s type=%s pid=%s log=%s",
                task_id, task_type, proc.pid, log_path)

    # 回收线程（reaper）：worker 退出后若无人 waitpid，会残留僵尸进程
    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("mining_worker 退出 task_id=%s type=%s pid=%s code=%s",
                        task_id, task_type, process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


def _task_timeout(task_type: str = None) -> int:
    """挖掘任务超时秒数，按类型分级（与 api/mining.py 保持一致）。"""
    from app.core.config import settings
    task_cfg = settings.task or {}
    timeouts = task_cfg.get("timeouts", {}) or {}
    if task_type and task_type in timeouts:
        return int(timeouts[task_type])
    return int(task_cfg.get("task_timeout_seconds", 300))


async def _mark_failed(task_id: int, error: str) -> None:
    """将挖掘任务标记为 failed（兜底，避免状态卡 running）。"""
    try:
        from app.services.mining.task_utils import update_task_status
        await update_task_status(task_id, status="failed", error=error,
                                 finished_at=datetime.now())
    except Exception:
        logger.exception("标记挖掘任务 failed 失败 task_id=%s", task_id)


async def _run(args: argparse.Namespace) -> None:
    # 注入 worker_kind 上下文，日志 JSON 行内携带该字段
    from app.core.logging_config import set_worker_kind

    set_worker_kind(f"mining:{args.type}")
    params = json.loads(args.params) if args.params else {}
    write_worker_pid(args.task_id)
    try:
        await _run_inner(args, params)
    finally:
        clear_worker_pid(args.task_id)


async def _run_inner(args: argparse.Namespace, params: dict) -> None:
    # 与 api/mining.py 保持一致的按类型超时；LLM 用 hard-limit 兜底
    timeout = _task_timeout(args.type)

    async def _inner():
        if args.type == "llm":
            from app.services.mining.llm_factor import mine_with_llm_iterative
            n_rounds = int(params.get("n_rounds", 1) or 1)
            n_candidates = params.get("n_candidates")
            await mine_with_llm_iterative(
                args.task_id, n_rounds=n_rounds, n_candidates=n_candidates,
                universe=params.get("universe"),
            )
        elif args.type == "symbolic":
            from app.services.mining.symbolic import mine_with_symbolic
            await mine_with_symbolic(args.task_id, universe=params.get("universe"))
        elif args.type == "automl":
            from app.services.mining.automl import mine_with_automl
            await mine_with_automl(
                args.task_id,
                params.get("factor_ids") or [],
                params.get("method"),
                walk_forward=bool(params.get("walk_forward", False)),
                universe=params.get("universe"),
            )
        elif args.type == "text":
            from app.services.mining.text_factor import mine_with_text
            await mine_with_text(args.task_id, params.get("codes"))
        else:
            raise ValueError(f"未知挖掘任务类型: {args.type}")

    try:
        await asyncio.wait_for(_inner(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("挖掘任务超时 task_id=%s type=%s (timeout=%ss)", args.task_id, args.type, timeout)
        await _mark_failed(args.task_id, f"任务超时 (timeout={timeout}s)")
        sys.exit(1)
    except Exception as e:
        # 各挖掘函数内部已把失败原因写入任务（error 字段），这里兜底防止
        # 未捕获异常导致进程退出但 DB 状态卡 running
        logger.exception("挖掘任务失败 task_id=%s type=%s", args.task_id, args.type)
        await _mark_failed(args.task_id, str(e)[:500])
        sys.exit(1)


def main() -> None:
    from app.core.config import settings

    parser = argparse.ArgumentParser(description="QuantLab 因子挖掘独立 worker")
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--type", choices=["llm", "symbolic", "automl", "text"], required=True)
    parser.add_argument("--params", default="{}")
    args = parser.parse_args()

    # 统一日志：与 web 进程共用 setup_logging（structlog JSON 管道）。
    # 挖掘 worker 写 mining.log，跨进程写入/轮转由 LockedRotatingFileHandler 保证安全。
    from app.core.logging_config import setup_logging

    setup_logging(
        log_dir=settings.PROJECT_ROOT / settings.logging.dir,
        level=settings.logging.level,
        console=False,
        log_file="mining.log",
        error_file=None,
    )

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        logging.getLogger(__name__).exception("mining_worker %s 异常退出", args.type)
        sys.exit(1)


if __name__ == "__main__":
    main()
