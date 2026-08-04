"""数据同步独立 worker（子进程运行，与 web 进程解耦）。

为什么需要独立子进程：
- FastAPI 的 BackgroundTasks 运行在 web 进程的事件循环里，uvicorn --reload 触发重载时
  会"等待后台任务完成"，而 baostock 全量回填/repair 可能跑几十分钟甚至数小时，
  导致 reload 永远等不完、新 worker 起不来、整个后端卡死。
- 本模块把这些长同步任务放到**独立子进程**里跑：
    - 不占用 web 事件循环 → 不阻塞 reload 退出
    - 独立进程组（start_new_session）→ web 进程重载/重启不会把它一起杀掉
- 状态写数据库（stock_data_status），进度镜像到共享文件（data/sync_progress.json），
  web 进程通过 /quant/data/sync-progress 与 /quant/data/status 继续向前端提供实时信息。

用法（CLI，web 进程通过 spawn_sync_worker 调用）:
    python -m app.services.data.sync_worker --kind backfill --universe all --years 5
    python -m app.services.data.sync_worker --kind eod --universe csi300 --days 5
    python -m app.services.data.sync_worker --kind repair --universe all --include-baostock
"""
import argparse
import asyncio
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)


def spawn_sync_worker(
    kind: str,
    universe: str,
    *,
    years: int = None,
    days: int = None,
    include_baostock: bool = False,
    overwrite: bool = False,
    source: str = None,
) -> subprocess.Popen:
    """启动一个独立的同步 worker 子进程并立即返回。

    start_new_session=True 使 worker 脱离 web 进程的进程组：
    - uvicorn --reload 重启时不会等待/杀掉它
    - web 进程崩溃也不影响它继续跑
    日志追加写 logs/sync_worker_<kind>.log。
    """
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    log_dir = settings.PROJECT_ROOT / "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(str(log_dir), f"sync_worker_{kind}.log")

    cmd = [sys.executable, "-m", "app.services.data.sync_worker",
           "--kind", kind, "--universe", universe]
    if years is not None:
        cmd += ["--years", str(years)]
    if days is not None:
        cmd += ["--days", str(days)]
    if include_baostock:
        cmd += ["--include-baostock"]
    if overwrite:
        cmd += ["--overwrite"]
    if source:
        cmd += ["--source", source]

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", backend_dir)
    env["PYTHONUNBUFFERED"] = "1"

    log_f = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=backend_dir,
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
            env=env,
        )
    except Exception:
        log_f.close()
        raise
    logger.info("sync_worker 已启动 kind=%s universe=%s pid=%s log=%s",
                kind, universe, proc.pid, log_path)

    # 回收线程（reaper）：worker 退出后若无人 waitpid，会残留僵尸进程，
    # 使 sync_progress._pid_alive 无法区分"在跑"与"已死"，进而导致
    # sync_is_active 误判活跃、阻塞后续同步。后台 wait 不阻塞调用方。
    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("sync_worker 退出 kind=%s universe=%s pid=%s code=%s",
                        kind, universe, process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


async def _run(args: argparse.Namespace) -> None:
    from app.services.data.sync_progress import (
        clear_progress, finish_progress, init_progress, set_worker_pid,
    )
    from app.services.data.sync_lock import SyncLock

    # 关键：先抢单实例爬取锁。抢不到说明已有爬取进程在跑 → 直接退出，什么都不碰
    # （不写进度/不写 DB，避免覆盖活跃 worker 的状态）。flock 由内核持有，
    # 进程死亡(含 kill -9)自动释放，因此锁永远不会卡住。
    lock = SyncLock()
    if not lock.try_acquire():
        logger.info("已有爬取进程在运行(sync.lock 被占用)，同步 worker 直接退出，避免并发连接 baostock")
        return
    logger.info("sync_worker 获取爬取锁成功 pid=%s", os.getpid())

    try:
        logger.info("sync_worker 开始: kind=%s universe=%s", args.kind, args.universe)
        await _run_crawl(args, init_progress, set_worker_pid, finish_progress, clear_progress)
        logger.info("sync_worker 完成: kind=%s universe=%s", args.kind, args.universe)
    finally:
        # 无论正常/异常，都要退出爬取锁（幂等）
        lock.release()


async def _run_crawl(args, init_progress, set_worker_pid, finish_progress, clear_progress) -> None:
    from app.services.data.baostock_client import baostock_session

    # 记录 worker PID，web 进程据此判断同步是否真的在跑。
    # data_source 按任务类型区分（baostock 特指全量回填，其余用 kind 名），
    # 前端据此显示任务标签。
    data_source = "baostock" if args.kind == "backfill" else args.kind
    init_progress(args.universe, data_source)
    set_worker_pid(os.getpid())

    # 仅数据源用到 baostock 时才 login/logout（保证退出前必然登出）：
    # - backfill / indices / eod(source=baostock) / repair(include_baostock) 需要
    # - repair(仅从 PG 重建) / eod(source=akshare) 不需要 baostock，
    #   即使 baostock 被风控拉黑也不受影响（离线重建路径保持可用）。
    need_bst = (
        args.kind == "backfill"
        or args.kind == "indices"
        or (args.kind == "repair" and args.include_baostock)
        or (args.kind == "eod" and (args.source or "baostock") == "baostock")
    )

    from contextlib import nullcontext
    from app.services.data.baostock_client import baostock_session

    session = baostock_session() if need_bst else nullcontext()
    try:
        with session:
            if args.kind == "backfill":
                from app.schemas.quant import SyncDataRequest
                from app.services.data.baostock_backfill import run_baostock_backfill_task

                req = SyncDataRequest(universe=args.universe, years=args.years)
                await run_baostock_backfill_task(req)
            elif args.kind == "eod":
                from app.core.config import settings
                from app.services.data.eod_incremental import incremental_sync_eod
                import json

                result_path = os.path.join(
                    str(settings.PROJECT_ROOT / "data"), "eod_last_result.json",
                )
                result = {"ok": False, "error": "unknown"}
                try:
                    result = await incremental_sync_eod(
                        universe=args.universe, days=args.days or 5, overwrite=args.overwrite,
                        source=args.source or "baostock",
                    )
                    finish_progress(bool(result.get("ok")), result.get("error"))
                finally:
                    try:
                        os.makedirs(os.path.dirname(result_path), exist_ok=True)
                        with open(result_path, "w", encoding="utf-8") as f:
                            json.dump(result, f, ensure_ascii=False, default=str)
                    except Exception:
                        pass
            elif args.kind == "repair":
                from app.services.data.repair import run_repair

                await run_repair(args.include_baostock, args.universe)
            elif args.kind == "indices":
                from app.core.config import settings
                from app.services.data.index_sync import sync_indices_to_qlib

                result = sync_indices_to_qlib(settings.qlib_provider_path, days=365)
                if result.get("ok"):
                    logger.info("指数同步完成: %s", result)
                else:
                    logger.error("指数同步返回错误: %s", result)

        # 留出前端轮询读取 done/failed 状态的窗口
        await asyncio.sleep(3)
        clear_progress()
    except Exception as e:
        # 登录/初始化阶段的异常（如 baostock 拉黑）发生在各任务内部错误处理之前，
        # 若直接崩溃退出，进度文件停在 downloading、DB 只会显示"[worker 退出]"通用提示。
        # 这里把真实错误写进进度文件并标记 DB，前端即可直接看到原因而非靠猜。
        try:
            finish_progress(False, str(e))
        except Exception:
            pass
        try:
            from app.services.data.baostock_backfill import mark_sync_failed
            await mark_sync_failed(args.universe, str(e))
        except Exception:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="QuantLab 数据同步独立 worker")
    parser.add_argument("--kind", choices=["backfill", "eod", "repair", "indices"], default="backfill")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--years", type=int, default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--include-baostock", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--source", choices=["baostock", "akshare"], default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        # 数据库状态由 run_baostock_backfill_task / run_repair 内部兜底标记 failed
        print(f"[sync_worker] {args.kind} 失败: {e}", file=sys.stderr)
        logging.getLogger(__name__).exception("sync_worker %s 异常退出", args.kind)
        sys.exit(1)


if __name__ == "__main__":
    main()
