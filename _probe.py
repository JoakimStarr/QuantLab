"""诊断符号回归程序翻译问题。"""
import asyncio
import sys
import json
import logging

sys.path.insert(0, "backend")
logging.basicConfig(level=logging.INFO)

async def main():
    from app.core.database import init_db
    await init_db()
    from app.core.config import settings
    settings.mining["symbolic"]["population"] = 200
    settings.mining["symbolic"]["generations"] = 5

    from app.services.mining.symbolic import _build_dataset, _translate_program, _BASE_FEATURES, _FUNC_MAP
    from app.services.factor.expression import validate_expression, ExpressionValidationError

    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")

    print("=== 构建数据集 ===")
    X, y, feature_names, _ = await asyncio.get_running_loop().run_in_executor(
        None, _build_dataset, start, end
    )
    print(f"X.shape={X.shape}, features={feature_names}")

    print("\n=== gplearn fit ===")
    from gplearn.genetic import SymbolicRegressor
    sym_cfg = settings.mining.get("symbolic", {})
    est = SymbolicRegressor(
        population_size=sym_cfg.get("population", 200),
        generations=sym_cfg.get("generations", 5),
        tournament_size=sym_cfg.get("tournament_size", 20),
        parsimony_coefficient=sym_cfg.get("parsimony_coefficient", 0.001),
        function_set=("add", "sub", "mul", "div"),
        n_jobs=1,
        random_state=42,
        verbose=0,
        metric="spearman",
    )
    await asyncio.get_running_loop().run_in_executor(None, est.fit, X, y)
    print("fit done")

    print("\n=== top programs ===")
    programs = est._programs[-1]
    valid_progs = [p for p in programs if p is not None and len(p.program) > 1]
    valid_progs.sort(key=lambda p: p.fitness_)
    top_progs = valid_progs[:5]
    print(f"top_progs count={len(top_progs)}")

    for i, prog in enumerate(top_progs):
        prog_str = str(prog)
        print(f"\n--- prog {i} (fitness={prog.fitness_}, len={len(prog.program)}) ---")
        print(f"  raw str: {prog_str[:300]!r}")
        print(f"  raw len: {len(prog_str)}")
        translated = _translate_program(prog_str, feature_names)
        print(f"  translated len: {len(translated)}")
        print(f"  translated (前 500): {translated[:500]!r}")
        try:
            validate_expression(translated, max_length=10000)
            print(f"  validate: OK")
        except ExpressionValidationError as e:
            print(f"  validate: REJECTED - {e}")
        # 也试试用 Python eval 看语法（仅诊断）
        import ast
        import re
        expr_for_ast = re.sub(r"\$([a-zA-Z_]+)", r"x_\1", translated)
        try:
            ast.parse(expr_for_ast, mode="eval")
            print(f"  ast.parse: OK")
        except SyntaxError as e:
            print(f"  ast.parse: FAILED - {e}")
            # 找到出错位置
            print(f"  出错附近: {expr_for_ast[max(0,e.offset-50):e.offset+50]!r}" if e.offset else "")


if __name__ == "__main__":
    asyncio.run(main())
