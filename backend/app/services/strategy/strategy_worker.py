"""策略任务独立 worker（子进程运行，与 web 进程解耦）。

承载三类长任务（均 CPU 密集、可达数分钟）：
- backtest      策略回测（qlib 工业级回测）
- walk-forward  滚动回测（多窗口 × 多 topk，最久）
- param-sweep   参数扫描（N×M 组合回测）

为什么独立子进程：
- 这三类任务原先跑在 web 进程的 BackgroundTasks 里，文件变更触发
  uvicorn --reload 时，解释器退出会等待在途任务/进程池结束——长回测会把
  reload 卡死、整个服务失去响应（与因子评价同源事故）。
- 独立子进程 + start_new_session=True 后：web 重启/崩溃不影响任务，
  uvicorn --reload 也不会等待它退出。

状态回传：
- backtest：成功写 backtest_result 表 + 策略状态置 active；失败置
  backtest_failed。web 进程内存 backtest_status 通过 pid 存活 + DB 推导对账。
- walk-forward / param-sweep：直接更新 task_result 表（status/payload/error），
  天然跨进程可读，前端轮询 task_result 即可。

用法（web 进程通过 spawn_strategy_worker 调用）:
    python -m app.services.strategy.strategy_worker --kind backtest \
        --strategy-id 1 --params '{"start":"2020-01-01","backend":"qlib"}'
    python -m app.services.strategy.strategy_worker --kind walk-forward \
        --strategy-id 1 --params '{"task_result_id":5,"train_window":"730D",...}'
    python -m app.services.strategy.strategy_worker --kind param-sweep \
        --strategy-id 1 --params '{"task_result_id":6,"topk_list":[10,20],"rebalance_list":["day","week"]}'
"""
import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

# 任务存活标记目录：data/strategy_pids/{kind}_{strategy_id}.pid
PID_DIR = None  # 懒初始化


def _pid_dir() -> str:
    global PID_DIR
    if PID_DIR is None:
        from app.core.config import settings
        PID_DIR = str(settings.PROJECT_ROOT / "data" / "strategy_pids")
    return PID_DIR


def _pid_path(kind: str, strategy_id: int) -> str:
    return os.path.join(_pid_dir(), f"{kind}_{strategy_id}.pid")


def write_worker_pid(kind: str, strategy_id: int) -> None:
    try:
        os.makedirs(_pid_dir(), exist_ok=True)
        with open(_pid_path(kind, strategy_id), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        logger.exception("写入策略任务 pid 失败 kind=%s strategy_id=%s", kind, strategy_id)


def clear_worker_pid(kind: str, strategy_id: int) -> None:
    try:
        p = _pid_path(kind, strategy_id)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        logger.exception("清理策略任务 pid 失败 kind=%s strategy_id=%s", kind, strategy_id)


def is_task_running(kind: str, strategy_id: int) -> bool:
    """该策略的该类任务是否正在执行（有存活的 worker 子进程）。"""
    p = _pid_path(kind, strategy_id)
    try:
        with open(p, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        clear_worker_pid(kind, strategy_id)
        return False
    except Exception:
        return False


def spawn_strategy_worker(kind: str, strategy_id: int, params: dict = None) -> subprocess.Popen:
    """启动独立策略任务 worker 子进程并立即返回。

    start_new_session=True：uvicorn --reload 重启不会等待/杀掉它。
    日志写入 logs/sync.log（worker_kind=strategy:{kind} 区分）。
    """
    from app.core.config import settings

    backend_dir = str(settings.PROJECT_ROOT / "backend")
    cmd = [
        sys.executable, "-m", "app.services.strategy.strategy_worker",
        "--kind", kind, "--strategy-id", str(strategy_id),
    ]
    if params:
        cmd += ["--params", json.dumps(params, ensure_ascii=False)]

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", backend_dir)
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=backend_dir,
        start_new_session=True,
        env=env,
    )
    logger.info("strategy_worker 已启动 kind=%s strategy_id=%s pid=%s",
                kind, strategy_id, proc.pid)

    def _reap(process: subprocess.Popen) -> None:
        try:
            code = process.wait()
            logger.info("strategy_worker 退出 kind=%s strategy_id=%s pid=%s code=%s",
                        kind, strategy_id, process.pid, code)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_reap, args=(proc,), daemon=True).start()
    return proc


# ---------------- 三类任务体（模块级，供 worker 调用） ----------------

async def _mark_backtest_failed(strategy_id: int, error: str) -> None:
    """回测失败：策略状态置 backtest_failed + 描述附错误。"""
    from app.core.database import async_session
    from app.models.strategy import Strategy
    async with async_session() as session:
        r = await session.get(Strategy, strategy_id)
        if r:
            r.status = "backtest_failed"
            r.description = (r.description or "") + f"\n[回测失败] {str(error)[:200]}"
            await session.commit()


async def run_backtest_task(strategy_id: int, params: dict) -> None:
    """策略回测：执行 run_strategy_backtest + 同步策略状态。"""
    from app.core.database import async_session
    from app.models.strategy import Strategy
    from app.services.strategy.manager import run_strategy_backtest
    try:
        await run_strategy_backtest(
            strategy_id,
            params.get("start"), params.get("end"),
            backend=params.get("backend", "qlib"),
            capital=params.get("initial_capital"),
            trade_unit=params.get("trade_unit"),
            deal_price=params.get("deal_price"),
            slippage_bps=params.get("slippage_bps"),
            cost_buy=params.get("cost_buy"),
            cost_sell=params.get("cost_sell"),
            min_cost=params.get("min_cost"),
            universe=params.get("universe"),
            asset_class=params.get("asset_class", "stock"),
        )
        # 成功：重置策略状态为 active（清除历史 backtest_failed 标记）
        async with async_session() as session:
            r = await session.get(Strategy, strategy_id)
            if r and r.status != "active":
                r.status = "active"
                if r.description and "[回测失败]" in r.description:
                    r.description = r.description.split("\n[回测失败]")[0].strip()
                await session.commit()
        logger.info("策略回测完成 strategy_id=%s", strategy_id)
    except Exception as e:
        logger.exception("策略回测失败 strategy_id=%s", strategy_id)
        await _mark_backtest_failed(strategy_id, str(e))
        sys.exit(1)


async def run_walk_forward_task(strategy_id: int, params: dict) -> None:
    """Walk-forward 滚动回测：构建 score_df + 滚动窗口回测 + 结果写 task_result。"""
    import asyncio

    from app.core.database import async_session
    from app.models.strategy import Strategy
    from app.models.task_result import TaskResult
    task_result_id = int(params["task_result_id"])

    try:
        from app.services.strategy.manager import _load_factor_expressions
        async with async_session() as session:
            s = await session.get(Strategy, strategy_id)
        if s is None:
            raise ValueError(f"策略 {strategy_id} 不存在")

        factor_ids = json.loads(s.factor_ids) if s.factor_ids else []
        factor_meta = await _load_factor_expressions(factor_ids)
        factor_exprs = {}
        weights = {}
        for fid in factor_ids:
            meta = factor_meta.get(fid)
            if not meta:
                continue
            factor_exprs[meta["name"]] = meta["expression"]
            weights[meta["name"]] = meta.get("ic") or 0.0
        if not factor_exprs:
            raise ValueError("策略无有效因子")

        from app.services.quant.walk_forward import build_score_df_from_exprs, run_walk_forward
        loop = asyncio.get_running_loop()
        start = params.get("start")
        end = params.get("end")
        universe = params.get("universe")
        score_df = await loop.run_in_executor(
            None, build_score_df_from_exprs,
            factor_exprs, weights, s.combination_method, start, end, universe,
        )
        result = await loop.run_in_executor(
            None, run_walk_forward,
            score_df, None,
            params.get("train_window", "730D"),
            params.get("test_window", "180D"),
            params.get("step", "180D"),
            params.get("topk_list", [10, 20, 30, 50]),
            int(params.get("n_drop", 5)),
            params.get("rebalance", "day"),
            0.0013, 0.0023, s.benchmark,
        )
        async with async_session() as session:
            r = await session.get(TaskResult, task_result_id)
            if r:
                r.status = "done"
                r.payload = json.dumps(result, default=str)
                await session.commit()
        logger.info("walk-forward 完成 strategy_id=%s", strategy_id)
    except Exception as e:
        logger.exception("walk-forward 失败 strategy_id=%s", strategy_id)
        async with async_session() as session:
            r = await session.get(TaskResult, task_result_id)
            if r:
                r.status = "failed"
                r.error = str(e)[:500]
                await session.commit()
        sys.exit(1)


async def run_param_sweep_task(strategy_id: int, params: dict) -> None:
    """参数扫描：N×M 组合回测 + 结果写 task_result。"""
    from app.core.database import async_session
    from app.models.task_result import TaskResult
    from app.services.strategy.param_sweep import run_param_sweep
    task_result_id = int(params["task_result_id"])

    try:
        results = await run_param_sweep(
            strategy_id,
            params.get("topk_list", [10, 20, 30, 50]),
            params.get("rebalance_list", ["day", "week"]),
            params.get("start"),
            params.get("end"),
            backend=params.get("backend", "qlib"),
        )
        async with async_session() as session:
            r = await session.get(TaskResult, task_result_id)
            if r:
                r.status = "done"
                r.payload = json.dumps(results, default=str)
                await session.commit()
        logger.info("参数扫描完成 strategy_id=%s (%d 组合)",
                    strategy_id, len(results))
    except Exception as e:
        logger.exception("参数扫描失败 strategy_id=%s", strategy_id)
        async with async_session() as session:
            r = await session.get(TaskResult, task_result_id)
            if r:
                r.status = "failed"
                r.error = str(e)[:500]
                await session.commit()
        sys.exit(1)


# ---------------- CLI ----------------

async def _run(args: argparse.Namespace) -> None:
    from app.core.logging_config import set_worker_kind

    set_worker_kind(f"strategy:{args.kind}")
    write_worker_pid(args.kind, args.strategy_id)
    params = json.loads(args.params) if args.params else {}
    try:
        if args.kind == "backtest":
            await run_backtest_task(args.strategy_id, params)
        elif args.kind == "walk-forward":
            await run_walk_forward_task(args.strategy_id, params)
        elif args.kind == "param-sweep":
            await run_param_sweep_task(args.strategy_id, params)
        else:
            raise ValueError(f"未知策略任务类型: {args.kind}")
    finally:
        clear_worker_pid(args.kind, args.strategy_id)


def main() -> None:
    from app.core.config import settings

    parser = argparse.ArgumentParser(description="QuantLab 策略任务独立 worker")
    parser.add_argument("--kind", choices=["backtest", "walk-forward", "param-sweep"],
                        required=True)
    parser.add_argument("--strategy-id", type=int, required=True)
    parser.add_argument("--params", default="{}")
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
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        logging.getLogger(__name__).exception("strategy_worker %s 异常退出", args.kind)
        sys.exit(1)


if __name__ == "__main__":
    main()
