"""经典策略库：教学向的经典策略注册表 + 可运行编排。

设计：每个经典策略 = 一张教学卡片（一句话逻辑 / 为什么有效 / 什么时候失效 / 文献），
并带一份可直接运行的默认配置。运行时按 kind 分发到现有两条管线：
    - kind=factor: 截面因子型（横截面动量/反转/低波等），复用 manager._compute_backtest_sync
      的 load_factor_values + combine_factors + run_backtest + analyze_portfolio 链路；
    - kind=rule: 单标的规则型（双均线/布林带/RSI 等），复用 rule_backtest.run_rule_backtest。
回测是阻塞计算，调用方必须经 run_in_executor / run_io_cpu 放入线程池。
"""
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# 截面因子型的默认调仓频率说明：动量/反转/低波均以周/月级截面排序调仓，
# 与单标的规则型的高频信号不同；这里统一给出行业常用默认可被前端覆盖。
CLASSIC_STRATEGIES: list[dict] = [
    # ---------------- 截面因子型（跨股票池排序，top-k 买入） ----------------
    {
        "key": "momentum",
        "name": "横截面动量",
        "category": "动量",
        "kind": "factor",
        "tagline": "过去一段时间的赢家，往往继续跑赢平均值。",
        "why_works": (
            "Jegadeesh & Titman (1993) 发现美股存在显著的 3-12 月价格惯性：投资者对信息反应不足"
            "（缓慢扩散），导致历史涨幅领先的股票收益延续。A 股在 20-60 日动量窗口也常观察到类似效应，"
            "叠加 A 股高波动、散户占比高的结构，动量效应有时比成熟市场更早浮现。"
        ),
        "when_fails": (
            "动量在急跌/牛熊转换期会剧烈反转——止盈盘与去杠杆集中释放时，前期涨得最多的股票砸得最狠；"
            "小盘高换手标的动量易被游资行情透支；长期（>1 年）动量在 A 股衰减明显。"
            "文献（Daniel & Moskowitz 2016）显示动量崩溃（momentum crash）多发生在市场反弹初期。"
        ),
        "reference": "Jegadeesh, N. & Titman, S. (1993). Returns to Buying Winners and Selling Losers. JF.",
        "expression": "$close / Ref($close, 60) - 1",
        "defaults": {"topk": 50, "n_drop": 5, "rebalance_freq": "month", "universe": "csi300"},
    },
    {
        "key": "short_reversal",
        "name": "短期反转",
        "category": "反转",
        "kind": "factor",
        "tagline": "过去短期跌得最多的股票，接下来容易反弹。",
        "why_works": (
            "短期（1-2 周）收益呈负自相关：流动性冲击与投资者过度反应造成超跌，随后价格向基本面回归"
            "（Lehmann 1990 在周频截面发现显著反转）。A 股 T+1 与涨跌停制度放大了短期拥挤交易，"
            "连板票见顶后常有均值回归，短反转在 A 股周频样本中属于较强的截面信号。"
        ),
        "when_fails": (
            "趋势强化的单边上涨（如主升浪）中，买入「输家」会持续接飞刀；退市风险股的不理性下跌"
            "并不回归基本面而是归零；反转在剔除北交所与小市值垃圾标的后的高流动性池里更稳定。"
            "若叠加系统性流动性收紧，反转收益会被整体 beta 回撤吞没。"
        ),
        "reference": "Lehmann, B. (1990). Fads, Martingales, and Market Efficiency. QJE.",
        "expression": "-1 * ($close / Ref($close, 5) - 1)",
        "defaults": {"topk": 50, "n_drop": 5, "rebalance_freq": "week", "universe": "csi500"},
    },
    {
        "key": "low_volatility",
        "name": "低波动异象",
        "category": "质量低波",
        "kind": "factor",
        "tagline": "长期看，低波动的稳健股票跑赢高波动的博弈股。",
        "why_works": (
            "Baker, Bradley & Wurgler (2011) 系统总结了低波动异象：机构考核与投资者偏好追逐高波动博彩式"
            "收益（彩票偏好），导致波动率被系统性高估、低波动股票被低估。A 股散户主导的题材炒作格外"
            "抬升了高波动股票价格，使买入低波动组合在中长期获得明显的风险调整后超额。"
        ),
        "when_fails": (
            "低波异象在强 beta 行情中跑输大盘（涨得慢）；若组合集中在公用事业/银行等防御板块，"
            "对行业轮动敏感时会阶段性卧倒。真正的风险是「波动率陷阱」：长期阴跌的低波股若基本面恶化，"
            "波动率反而低位失真，需结合质量因子过滤。"
        ),
        "reference": "Baker, M., Bradley, B. & Wurgler, J. (2011). Benchmarks as Limits to Arbitrage. JFE.",
        "expression": "-1 * Std($close / Ref($close, 1) - 1, 20)",
        "defaults": {"topk": 50, "n_drop": 5, "rebalance_freq": "month", "universe": "csi300"},
    },
    {
        "key": "liquidity_premium",
        "name": "流动性溢价",
        "category": "截面因子",
        "kind": "factor",
        "tagline": "低流动性（低换手）的股票，长期补偿投资者更高的收益。",
        "why_works": (
            "Amihud & Mendelson (1986) 提出流动性溢价：持有流动性差的资产需要更高期望收益作为补偿。"
            "A 股市值小、关注度低的股票通常换手率低，被流动性约束的机构资金回避，留出折价空间；"
            "这里的实现以 20 日平均换手估计的相反数作为得分（低换手 = 高分）。"
        ),
        "when_fails": (
            "纯低换手组合可能误买入「僵尸股」（成交萎缩、基本面恶化），在流动性收缩期无法及时退出；"
            "被游资突袭拉升的低换手小票反转剧烈。适合作为组合中的风格倾斜而非单因子裸奔，"
            "最好叠加市值/质量过滤。"
        ),
        "reference": "Amihud, Y. & Mendelson, H. (1986). Asset Pricing and the Bid-Ask Spread. JFE.",
        "expression": "-1 * Mean($volume / Ref($close, 1) / 10000, 20)",
        "defaults": {"topk": 50, "n_drop": 5, "rebalance_freq": "month", "universe": "csi500"},
    },
    # ---------------- 单标的规则型（技术信号 long/flat） ----------------
    {
        "key": "dual_ma",
        "name": "双均线趋势跟随",
        "category": "趋势",
        "kind": "rule",
        "tagline": "快线上穿慢线买入趋势，下穿离场——经典趋势由移动平均定义。",
        "why_works": (
            "移动平均过滤噪音、刻画趋势方向（格兰维尔八大法则）。趋势跟随有效的本质是捕捉价格的非对称"
            "持续性：A 股政策/资金驱动的中级行情往往呈现清晰趋势段，双均线在趋势型标的上能吃到大部分"
            "主升浪。参数（10/30）快速跟随，适合中小波段。"
        ),
        "when_fails": (
            "震荡市中被反复「打脸」（两头挨打）；强反转行情中均线滞后导致回吐利润。"
            "参数越短越灵敏但噪音越重，越长越抗震荡但滞后越明显——需要匹配标的主升浪节奏并设好离场纪律。"
        ),
        "reference": "Granville, J. (1960). A New Strategy of Daily Stock Market Timing.",
        "rule_template": "ma_cross",
        "rule_params": {"fast": 10, "slow": 30},
    },
    {
        "key": "bollinger",
        "name": "布林带均值回归",
        "category": "均值回归",
        "kind": "rule",
        "tagline": "价格触及下轨时超跌回归，触中轨离场——震荡市的网格化；常被拉长区间打飞。",
        "why_works": (
            "Bollinger (1983) 用移动均线 ± k 倍标准差刻画价格常态区间；波动收敛后定价趋于均值。"
            "在无明确趋势的震荡标的里，价格偏离下轨的统计回归收益明显，尤其适合波动率均值回归的品种。"
        ),
        "when_fails": (
            "单边趋势行情中「抄底下轨」会持续亏损（趋势 + 放量下破）；波动率膨胀（「喇叭形」）时"
            "标准差无法框住价格。该策略本质赌波动收敛，需在趋势明确的标的上禁用或叠加方向过滤。"
        ),
        "reference": "Bollinger, J. (1992). Bollinger on Bollinger Bands.",
        "rule_template": "bollinger",
        "rule_params": {"window": 20, "k": 2.0},
    },
    {
        "key": "rsi",
        "name": "RSI 超买超卖",
        "category": "均值回归",
        "kind": "rule",
        "tagline": "RSI 记录涨跌强弱，超卖回升买入、超买离场——强弱势指标经典用法。",
        "why_works": (
            "Wilder (1978) 以 N 日内涨幅均值占比度量买卖双方动能；RSI 进入超卖区后再回升提示抛压衰竭。"
            "A 股题材股的脉冲式行情常把 RSI 打到极值随后回归，该策略适合均线修正式波段而非主升浪追价。"
        ),
        "when_fails": (
            "强趋势中 RSI 会在超买区钝化（钝化本身是强势信号，此时卖出会踏空）；超卖后在恐慌下跌中"
            "继续超卖（个股基本面恶化时的「跌到最低低」）。需要结合标的趋势层级使用。"
        ),
        "reference": "Wilder, J. W. (1978). New Concepts in Technical Trading Systems.",
        "rule_template": "rsi",
        "rule_params": {"period": 14, "oversold": 30, "overbought": 70},
    },
    {
        "key": "turtle",
        "name": "唐奇安通道突破",
        "category": "趋势",
        "kind": "rule",
        "tagline": "突破 N 日新高入场、跌破 N 日新低离场——海龟交易法的核心规则。",
        "why_works": (
            "Richard Donchian 的海龟交易系统以「突破加仓、破位离场」刻画强趋势：成交量与价格同步放大突破"
            "关键阻力后往往形成持续性行情。规则简单、避免预测，靠少数大趋势盈利覆盖多数小亏损，"
            "是趋势跟随的祖师爷策略。"
        ),
        "when_fails": (
            "假突破频发的窄幅震荡箱体（突破即回踩）；跳空高开追入成本过高。"
            "海龟系统强调分散与加仓纪律，单一标的裸跑样本太小，波动完全依赖标的自身趋势质量。"
        ),
        "reference": "Faith, C. (2007). Way of the Turtle (Donchian Channel).",
        "rule_template": "momentum",
        "rule_params": {"window": 20},
    },
]


def list_classic_strategies() -> list[dict]:
    """教学卡片列表（不含内部生成器，仅返回可展示/可运行的信息）。"""
    items = []
    for s in CLASSIC_STRATEGIES:
        card = {
            "key": s["key"],
            "name": s["name"],
            "category": s["category"],
            "kind": s["kind"],
            "tagline": s["tagline"],
            "why_works": s["why_works"],
            "when_fails": s["when_fails"],
            "reference": s["reference"],
            "defaults": s["defaults"] if s["kind"] == "factor" else s["rule_params"],
        }
        if s["kind"] == "factor":
            card["expression"] = s["expression"]
        else:
            card["rule_template"] = s["rule_template"]
        items.append(card)
    return items


def get_classic_strategy(key: str) -> dict | None:
    for s in CLASSIC_STRATEGIES:
        if s["key"] == key:
            return s
    return None


def _run_factor_classic(spec: dict, params: dict, start: str, end: str) -> dict:
    """运行截面因子型经典策略：复用策略回测的同一条因子 topk 链路。

    params 可覆盖: topk/n_drop/rebalance_freq/universe/benchmark/backend/capital 等。
    """
    from app.services.strategy.manager import _compute_backtest_sync

    defaults = spec["defaults"]
    topk = int(params.get("topk", defaults.get("topk", 50)))
    n_drop = int(params.get("n_drop", defaults.get("n_drop", 5)))
    rebalance_freq = params.get("rebalance_freq", defaults.get("rebalance_freq", "month"))
    universe = params.get("universe", defaults.get("universe", "csi300"))
    benchmark = params.get("benchmark") or settings.quant.get("benchmark", "SH000300")

    # 教学默认只做多（long-only）：买入得分最高的一篮子。方向已编码进表达式。
    factor_exprs = {spec["name"]: spec["expression"]}
    weights = {spec["name"]: 1.0}

    return _compute_backtest_sync(
        factor_exprs, weights, "equal_weight",
        topk, n_drop, benchmark, rebalance_freq, start, end,
        orthogonalize=0,
        backend=params.get("backend", "qlib"),
        capital=params.get("initial_capital"),
        trade_unit=params.get("trade_unit"),
        deal_price=params.get("deal_price"),
        slippage_bps=params.get("slippage_bps"),
        cost_buy=params.get("cost_buy"),
        cost_sell=params.get("cost_sell"),
        min_cost=params.get("min_cost"),
        universe=universe,
        asset_class=params.get("asset_class", "stock"),
    )


def _run_rule_classic(spec: dict, params: dict, start: str, end: str) -> dict:
    """运行单标的规则型经典策略：复用策略库的 rule_backtest 模板管线。"""
    from app.services.quant.rule_backtest import run_rule_backtest

    capital = params.get("initial_capital") or settings.quant.get("initial_capital", 10_000_000)
    symbols = params.get("symbols") or []
    if not symbols:
        raise ValueError("规则型经典策略需要至少一个标的（symbols）")
    bt_params = {**spec["rule_params"], **params.get("rule_params", {})}
    benchmark = params.get("benchmark") or settings.quant.get("benchmark", "SH000300")
    return run_rule_backtest(
        spec["rule_template"], bt_params, symbols, start, end, benchmark, capital,
    )


def run_classic_strategy(key: str, params: dict | None, start: str, end: str) -> dict:
    """经典策略统一入口：按 kind 分发到因子 topk / 规则模板回测。"""
    params = params or {}
    spec = get_classic_strategy(key)
    if spec is None:
        raise ValueError(f"未知经典策略: {key}")
    if spec["kind"] == "factor":
        result = _run_factor_classic(spec, params, start, end)
    else:
        result = _run_rule_classic(spec, params, start, end)
    # 与规则回测相同：指标展开到顶层（BacktestResultDetail/列表页通用字段），保留 metrics 嵌套。
    result.update({k: result["metrics"].get(k) for k in (
        "annual_return", "annual_volatility", "sharpe", "sortino",
        "max_drawdown", "calmar", "win_rate", "benchmark_return", "excess_return",
    )} if result.get("metrics") else {})
    result["key"] = key
    result["name"] = spec["name"]
    result["kind"] = spec["kind"]
    result["category"] = spec["category"]
    result["initial_capital"] = result.get("initial_capital") or params.get("initial_capital")
    result["n_trades"] = len(result.get("trades") or [])
    if spec["kind"] == "factor":
        defaults = spec["defaults"]
        result["topk"] = params.get("topk", defaults.get("topk"))
        result["n_drop"] = params.get("n_drop", defaults.get("n_drop"))
        result["rebalance_freq"] = params.get("rebalance_freq", defaults.get("rebalance_freq"))
        result["benchmark"] = params.get("benchmark") or settings.quant.get("benchmark", "SH000300")
    else:
        result["params"] = result.get("params") or params.get("rule_params", {})
    return result