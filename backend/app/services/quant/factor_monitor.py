"""因子衰减监控：检测因子近期 IC 相对历史 IC 的衰减，定时检测并推送告警。"""
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


async def detect_factor_decay(
    factor_id: int,
    recent_days: int = 30,
    decay_threshold: float = 0.5,
    db_session=None,
) -> dict:
    """检测单个因子的衰减情况

    Args:
        factor_id: 因子ID
        recent_days: 近期交易日天数
        decay_threshold: 衰减阈值（近期IC / 历史IC < threshold 则判定衰减）
        db_session: 数据库会话

    Returns:
        {
            "factor_id": int,
            "factor_name": str,
            "historical_ic": float,
            "recent_ic": float,
            "decay_ratio": float,
            "is_decaying": bool,
            "status": "healthy" / "decaying" / "unknown",
        }
    """
    from app.models.factor import Factor
    from app.services.quant.factor_eval import load_factor_values, load_label, compute_ic
    from sqlalchemy import select

    result = await db_session.execute(select(Factor).where(Factor.id == factor_id))
    factor = result.scalars().first()
    if not factor:
        return {"factor_id": factor_id, "status": "unknown", "error": "因子不存在"}

    # 日期窗口：end=今天，start 往前推 recent_days*8 个自然日，保证历史段足够长
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=recent_days * 8)).strftime("%Y-%m-%d")

    try:
        # load_factor_values / load_label 为同步阻塞函数，放到线程池避免阻塞事件循环
        factor_df = await asyncio.to_thread(
            load_factor_values, factor.expression, start_date, end_date
        )
        if factor_df is None or factor_df.empty:
            return {"factor_id": factor_id, "factor_name": factor.name, "status": "unknown"}

        label_df = await asyncio.to_thread(load_label, start_date, end_date)
        if label_df is None or label_df.empty:
            return {"factor_id": factor_id, "factor_name": factor.name, "status": "unknown"}

        # 取实际存在的交易日，按日期切分历史/近期
        dates = sorted(factor_df.index.get_level_values("datetime").unique())
        if len(dates) < recent_days + 10:
            return {"factor_id": factor_id, "factor_name": factor.name, "status": "unknown"}

        recent_cutoff = dates[-recent_days]

        hist_mask = factor_df.index.get_level_values("datetime") < recent_cutoff
        recent_mask = factor_df.index.get_level_values("datetime") >= recent_cutoff

        historical_factor = factor_df[hist_mask]
        recent_factor = factor_df[recent_mask]
        historical_label = label_df[label_df.index.get_level_values("datetime") < recent_cutoff]
        recent_label = label_df[label_df.index.get_level_values("datetime") >= recent_cutoff]

        hist_ic_result = compute_ic(historical_factor, historical_label)
        recent_ic_result = compute_ic(recent_factor, recent_label)

        hist_ic = abs(hist_ic_result.get("ic") or 0)
        recent_ic = abs(recent_ic_result.get("ic") or 0)

        if hist_ic < 0.001:
            return {
                "factor_id": factor_id,
                "factor_name": factor.name,
                "historical_ic": float(hist_ic),
                "recent_ic": float(recent_ic),
                "decay_ratio": 0,
                "is_decaying": False,
                "status": "unknown",
            }

        decay_ratio = recent_ic / hist_ic
        is_decaying = decay_ratio < decay_threshold

        return {
            "factor_id": factor_id,
            "factor_name": factor.name,
            "expression": factor.expression,
            "category": factor.category,
            "historical_ic": float(hist_ic),
            "recent_ic": float(recent_ic),
            "decay_ratio": float(decay_ratio),
            "is_decaying": bool(is_decaying),
            "status": "decaying" if is_decaying else "healthy",
        }
    except (FileNotFoundError, ValueError) as e:
        # AutoML bundle 丢失 / 文本算子不可用等不可恢复错误：降级 WARNING 跳过，不刷 ERROR
        logger.warning("因子 %s 衰减检测跳过: %s", factor_id, e)
        return {"factor_id": factor_id, "factor_name": factor.name, "status": "unknown", "error": str(e)}
    except Exception as e:
        logger.error("因子 %s 衰减检测失败: %s", factor_id, e)
        return {"factor_id": factor_id, "factor_name": factor.name, "status": "unknown", "error": str(e)}


async def detect_all_factors_decay(db_session=None) -> dict:
    """检测所有 active 因子的衰减情况

    Returns:
        {
            "total": int,
            "decaying": int,
            "healthy": int,
            "decaying_factors": List[dict],
            "all_results": List[dict],
        }
    """
    from app.models.factor import Factor
    from sqlalchemy import select

    result = await db_session.execute(select(Factor).where(Factor.status == "active"))
    factors = result.scalars().all()

    decaying_factors: List[dict] = []
    all_results: List[dict] = []

    # 并发检测（限制并发数避免内存/IO 爆炸）
    sem = asyncio.Semaphore(4)

    async def _check_one(factor):
        # 每个检测使用独立 session，避免 AsyncSession 并发使用出错
        from app.core.database import async_session
        async with sem:
            async with async_session() as session:
                return await detect_factor_decay(factor.id, db_session=session)

    tasks = [_check_one(f) for f in factors]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for factor, decay_result in zip(factors, results):
        if isinstance(decay_result, Exception):
            logger.error("因子 %s 衰减检测异常: %s", factor.name, decay_result)
            all_results.append({
                "factor_id": factor.id,
                "factor_name": factor.name,
                "status": "unknown",
                "error": str(decay_result),
            })
            continue
        all_results.append(decay_result)
        if decay_result.get("is_decaying"):
            decaying_factors.append(decay_result)
            logger.warning(
                "因子衰减告警: %s (id=%s), 历史IC=%.4f, 近期IC=%.4f, 衰减比=%.2f",
                factor.name,
                factor.id,
                decay_result.get("historical_ic", 0),
                decay_result.get("recent_ic", 0),
                decay_result.get("decay_ratio", 0),
            )

    logger.info(
        "因子衰减检测完成: %d 个因子, %d 个衰减", len(factors), len(decaying_factors)
    )

    return {
        "total": len(factors),
        "decaying": len(decaying_factors),
        "healthy": len(factors) - len(decaying_factors),
        "decaying_factors": decaying_factors,
        "all_results": all_results,
    }


async def run_decay_check():
    """定时任务入口：检测所有因子衰减并推送 WebSocket 告警"""
    from app.core.database import async_session
    from app.core.websocket_manager import ws_manager

    async with async_session() as session:
        result = await detect_all_factors_decay(db_session=session)

    if result["decaying"] > 0:
        await ws_manager.broadcast(
            "factor_decay_alert",
            {
                "decaying_count": result["decaying"],
                "total": result["total"],
                "decaying_factors": result["decaying_factors"][:20],
            },
        )
        logger.warning("因子衰减告警推送: %d 个因子衰减", result["decaying"])

    return result
