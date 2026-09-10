"""因子评价/补算独立 worker（子进程运行，与 web 进程解耦）。

与 mining_worker / sync_worker 同款设计：
- 长任务（因子 IC 评价可到分钟级）跑在独立进程组（start_new_session=True），
  uvicorn --reload 重启不杀任务、不占 web 事件循环；
- 任务状态写 factor_eval_job 表，web 进程通过 /factors/eval-jobs 提供进度；
- PID 标记文件 data/eval_pids/{job_id}.pid 供 web 进程判断存活，
  web 重启时避免把仍存活的 worker 误标 failed。

用法（CLI，web 进程通过 spawn_factor_eval_worker 调用）:
    python -m app.services.factor.factor_eval_worker --job-id 42
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

PID_DIR = None  # 懒初始化：settings.PROJECT_ROOT / "data" / "eval_pids"


def _pid_dir() -> str:
    global PID_DIR
    if PID_DIR is None:
        from app.core.config import settings

        PID_DIR = str(settings.PROJECT_ROOT / "data" / "eval_pids")
    return PID_DIR


def _pid_path(job_id: int) -> str:
    return os.path.join(_pid_dir(), f"{job_id}.pid")


def write_worker_pid(job_id: int) -> None:
    try:
        os.makedirs(_pid_dir(), exist_ok=True)
        with open(_pid_path(job_id), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        logger.exception("写入 factor_eval worker pid 失败 job_id=%s", job_id)


def clear_worker_pid(job_id: int) -> None:
    try:
        p = _pid_path(job_id)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        logger.exception("清理 factor_eval worker pid 失败 job_id=%s", job_id)


def is_eval_worker_alive(job_id: int) -> bool:
    p = _pid_path(job_id)
    try:
        with open(p, encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        clear_worker_pid(job_id)
        return False
    except Exception:
        return False


def spawn_factor_eval_worker(job_id: int) -> subprocess.Popen:
    """启动独立的因子评价 worker 子进程并立即返回。"""
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    log_path = str(settings.PROJECT_ROOT / "logs" / "eval.log")

    cmd = [
        sys.executable, "-m", "app.services.factor.factor_eval_worker",
        "--job-id", str(job_id),
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
    logger.info("factor_eval_worker 已启动 job_id=%s pid=%s log=%s", job_id, proc.pid, log_path)

    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("factor_eval_worker 退出 job_id=%s pid=%s code=%s", job_id, process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


# ----------------------------- 任务执行 -----------------------------

def _preload_shared(start: str, end: str, universe: str, horizon: int):
    """任务级预加载：主 horizon 标签 + $close，供该 job 所有因子复用。

    避免每个因子在 evaluate_factor 内重复 load_label / compute_decay 的
    $close 全量 qlib 读取（原本每因子约 6 次全量加载）。
    """
    from app.services.quant import factor_eval as fe

    label_expr = fe.forward_return_label(horizon)
    label_df = fe.load_label(start, end, label_expr=label_expr, universe=universe)
    close_df = fe.load_close_df(start, end, universe)
    return label_df, close_df


def _evaluate_one(
    expr: str, start: str, end: str, universe: str,
    horizon: int, preloaded_label_df=None, preloaded_close_df=None,
) -> dict:
    """同步执行单因子评价（worker 内以 to_thread 运行，不嵌套进程池）。"""
    from app.services.quant import factor_eval as fe

    # 与 api 手动评价一致：额外计算 1/10/20 天周期 IC
    return fe.evaluate_factor(
        expr, start, end, universe=universe,
        horizon=horizon, horizons=[1, 5, 10, 20],
        preloaded_label_df=preloaded_label_df,
        preloaded_close_df=preloaded_close_df,
    )


async def _load_factor_expr(factor_id: int) -> str:
    from app.core.database import async_session
    from app.models.factor import Factor

    async with async_session() as session:
        row = await session.get(Factor, factor_id)
        return row.expression if row else None


async def _update_factor_metrics(factor_id: int, metrics: dict) -> None:
    from app.services.factor.library import update_factor_metrics

    await update_factor_metrics(factor_id, metrics)


async def _load_job(job_id: int):
    from app.core.database import async_session
    from app.models.factor_eval_job import FactorEvalJob

    async with async_session() as session:
        job = await session.get(FactorEvalJob, job_id)
        return job


async def _run_inner(job_id: int) -> None:
    from app.core.database import async_session
    from app.models.factor_eval_job import FactorEvalJob

    # 拉取 job 并置 running
    async with async_session() as session:
        job = await session.get(FactorEvalJob, job_id)
        if job is None:
            return
        if job.status == "cancelled":
            return
        try:
            factor_ids = json.loads(job.factor_ids or "[]")
        except ValueError:
            factor_ids = []
        payload = {
            "kind": job.kind,
            "factor_ids": factor_ids,
            "start_date": job.start_date,
            "end_date": job.end_date,
            "universe": job.universe,
            "id": job_id,
        }
        job.status = "running"
        job.started_at = datetime.now()
        job.total = len(factor_ids)
        job.done = 0
        job.error = None
        await session.commit()

    # 任务级预加载 label + $close：所有因子共用，避免逐因子重复全量 qlib IO。
    # 预加载失败（如数据缺失）时降级为 None，由 evaluate_factor 自行加载，行为不变。
    from app.core.config import settings

    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    preloaded_label_df = None
    preloaded_close_df = None
    try:
        preloaded_label_df, preloaded_close_df = await asyncio.to_thread(
            _preload_shared, payload["start_date"], payload["end_date"],
            payload["universe"], horizon,
        )
    except Exception:  # noqa: BLE001
        logger.warning("预加载 label/$close 失败，回退逐因子加载", exc_info=True)

    result_map = {}
    ok = 0
    failed = 0
    for i, fid in enumerate(payload["factor_ids"]):
        # 取消检查点
        async with async_session() as session:
            job = await session.get(FactorEvalJob, job_id)
            if job is None or job.status == "cancelled":
                return
            job.current_label = f"因子 #{fid}"
            await session.commit()

        expr = await _load_factor_expr(fid)
        if not expr:
            result_map[str(fid)] = {"error": "因子不存在"}
            failed += 1
        else:
            try:
                metrics = await asyncio.to_thread(
                    _evaluate_one, expr, payload["start_date"], payload["end_date"],
                    payload["universe"], horizon, preloaded_label_df, preloaded_close_df,
                )
                await _update_factor_metrics(fid, metrics)
                result_map[str(fid)] = {
                    "ic": metrics.get("ic"),
                    "rank_ic": metrics.get("rank_ic"),
                    "icir": metrics.get("icir"),
                    "turnover": metrics.get("turnover"),
                }
                ok += 1
            except Exception as e:  # noqa: BLE001
                logger.exception("因子评价失败 factor_id=%s", fid)
                result_map[str(fid)] = {"error": str(e)[:300]}
                failed += 1

        # 每个因子完成后更新进度（部分成功也逐条落库，崩溃只丢一个因子）
        async with async_session() as session:
            job = await session.get(FactorEvalJob, job_id)
            if job is None:
                return
            job.done = i + 1
            job.result = json.dumps(result_map, ensure_ascii=False)
            await session.commit()

    async with async_session() as session:
        job = await session.get(FactorEvalJob, job_id)
        if job is None:
            return
        job.status = "done" if failed == 0 else "failed"
        if failed:
            job.error = f"{ok} 成功 / {failed} 失败"
        job.done = len(payload["factor_ids"])
        job.result = json.dumps(result_map, ensure_ascii=False)
        job.finished_at = datetime.now()
        job.current_label = None
        await session.commit()
    logger.info("factor_eval job 完成 job_id=%s ok=%d failed=%d", job_id, ok, failed)


async def _run(args: argparse.Namespace) -> None:
    from app.core.logging_config import set_worker_kind

    set_worker_kind("factor_eval")
    write_worker_pid(args.job_id)
    try:
        await _run_inner(args.job_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("factor_eval worker 失败 job_id=%s", args.job_id)
        try:
            from app.core.database import async_session
            from app.models.factor_eval_job import FactorEvalJob

            async with async_session() as session:
                job = await session.get(FactorEvalJob, args.job_id)
                if job is not None and job.status in ("pending", "running"):
                    job.status = "failed"
                    job.error = str(e)[:500]
                    job.finished_at = datetime.now()
                    await session.commit()
        except Exception:
            logger.exception("标记 factor_eval job failed 失败 job_id=%s", args.job_id)
    finally:
        clear_worker_pid(args.job_id)


def main() -> None:
    from app.core.config import settings

    parser = argparse.ArgumentParser(description="QuantLab 因子评价独立 worker")
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()

    from app.core.logging_config import setup_logging

    setup_logging(
        log_dir=settings.PROJECT_ROOT / settings.logging.dir,
        level=settings.logging.level,
        console=False,
        log_file="eval.log",
        error_file=None,
    )

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        logging.getLogger(__name__).exception("factor_eval_worker %s 异常退出", args.job_id)
        sys.exit(1)


if __name__ == "__main__":
    main()
