"""量化平台端到端验证脚本。

在 Python 3.11 + pyqlib 环境运行：
    cd backend && python -m app.services.quant.verify_pipeline

验证项：
1. qlib 安装与初始化
2. 股票数据是否已同步（否则提示先调 /quant/data/sync）
3. 因子评价（内置因子 IC）
4. 因子组合 + 回测
5. 组合绩效指标
6. AI 挖掘（LLM，需配置 API Key）
"""
import asyncio
import sys
from app.core.database import init_db


async def main():
    print("=" * 60)
    print("量化平台端到端验证")
    print("=" * 60)

    # 1. qlib 初始化
    print("\n[1/6] qlib 初始化...")
    from app.services.quant.qlib_init import init_qlib, QlibNotAvailableError
    try:
        init_qlib()
        print("  ✓ qlib 已初始化")
    except QlibNotAvailableError as e:
        print(f"  ✗ {e}")
        print("  请在 Python 3.11 环境执行: pip install pyqlib")
        sys.exit(1)

    # 2. 数据检查
    print("\n[2/6] 股票数据检查...")
    from qlib.data import D
    try:
        instruments = D.instruments(market="csi300")
        df = D.features(instruments, ["$close"], start_time="2024-01-01", end_time="2024-06-01")
        if df is None or df.empty:
            print("  ✗ 无数据，请先同步: curl -X POST 'http://localhost:8000/api/v1/quant/data/sync'")
            sys.exit(1)
        print(f"  ✓ 数据就绪，样本行数: {len(df)}")
    except Exception as e:
        print(f"  ✗ 数据加载失败: {e}")
        print("  请先同步: curl -X POST 'http://localhost:8000/api/v1/quant/data/sync'")
        sys.exit(1)

    # 3. 因子评价
    print("\n[3/6] 因子评价（内置动量因子）...")
    from app.services.quant.factor_eval import evaluate_factor
    try:
        metrics = evaluate_factor("Ref($close, -20) / $close - 1", "2024-01-01", "2024-06-01")
        print(f"  ✓ IC={metrics.get('ic')} RankIC={metrics.get('rank_ic')} ICIR={metrics.get('icir')}")
    except Exception as e:
        print(f"  ✗ 因子评价失败: {e}")

    # 4. 因子组合 + 回测
    print("\n[4/6] 因子组合 + 回测...")
    from app.services.quant.factor_eval import load_factor_values
    from app.services.quant.backtest_engine import combine_factors, run_backtest
    try:
        f1 = load_factor_values("Ref($close, -20) / $close - 1", "2024-01-01", "2024-06-01")
        f2 = load_factor_values("Std($close / Ref($close, 1) - 1, 20)", "2024-01-01", "2024-06-01")
        score = combine_factors({"mom": f1, "vol": f2}, method="equal_weight")
        bt = run_backtest(score, start="2024-01-01", end="2024-06-01", topk=30, n_drop=3)
        print(f"  ✓ 回测完成，收益序列长度: {len(bt.get('returns') or [])}")
    except Exception as e:
        print(f"  ✗ 回测失败: {e}")

    # 5. 组合绩效
    print("\n[5/6] 组合绩效指标...")
    from app.services.quant.portfolio import analyze_portfolio, build_nav_curve
    try:
        returns = bt.get("returns")
        benchmark = bt.get("benchmark")
        m = analyze_portfolio(returns, benchmark)
        print(f"  ✓ 夏普={m.get('sharpe')} 年化={m.get('annual_return')} 回撤={m.get('max_drawdown')}")
        curve = build_nav_curve(returns, benchmark)
        print(f"  ✓ 净值曲线点数: {len(curve['dates'])}")
    except Exception as e:
        print(f"  ✗ 绩效计算失败: {e}")

    # 6. AI 挖掘（LLM）
    print("\n[6/6] AI 因子挖掘（LLM，需 API Key）...")
    import os
    if not os.getenv("GLM_API_KEY") and not os.getenv("SILICONFLOW_API_KEY"):
        print("  ⊙ 未配置 GLM_API_KEY/SILICONFLOW_API_KEY，跳过（非必需）")
    else:
        from app.core.database import async_session
        from app.models.mining_task import MiningTask
        from app.services.mining.llm_factor import mine_with_llm
        await init_db()
        async with async_session() as session:
            t = MiningTask(type="llm", status="pending", params='{"n":3}')
            session.add(t)
            await session.commit()
            await session.refresh(t)
            tid = t.id
        try:
            r = await mine_with_llm(tid, n_candidates=3)
            print(f"  ✓ LLM 挖掘完成: 生成 {r['generated']} 通过 {r['passed']} 最佳IC={r.get('best_ic')}")
        except Exception as e:
            print(f"  ✗ LLM 挖掘失败: {e}")

    print("\n" + "=" * 60)
    print("验证完成。qlib 依赖项均通过则量化平台运行就绪。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
