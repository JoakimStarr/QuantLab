"""点名板块 × 市场表现：把 AI 点名的板块名匹配到证监会行业，用成分股等权收益代表板块 T+N 日表现。

背景：AI 解读产出 sectors（自由文本板块名，如「AI算力」「电力/储能」），项目没有行业指数行情，
因此用「板块名 → 证监会行业关键词匹配 → 成分股等权 pct_chg」近似板块表现。
匹配不上的板块返回 industry=None（前端标注「暂无对照」），不臆造数据。

性能：窗口内一次性批量查询行业成分股 + 相关交易日行情，内存聚合，无 N+1。
"""
from __future__ import annotations

import asyncio
import logging
from bisect import bisect_right
from datetime import date, timedelta

from sqlalchemy import select, true

from app.core.database import async_session
from app.models.baostock import StockBasic, StockDaily, StockIndustry, TradeCalendar
from app.models.policy import PolicyAnalysis

logger = logging.getLogger(__name__)

# 市场基准：对照板块等权收益的「同期大盘」。取沪深300（qlib bin sh000300），
# 与板块口径一致用单日 pct_chg（同一 T+N 交易日）。qlib 不可用时返回空，前端显示 '—'。
BENCHMARK_CODE = "sh000300"
BENCHMARK_NAME = "沪深300"
# 每个板块展示的龙头股数（按 T+1 涨幅排序）
TOP_STOCKS = 5

# 板块名特征词 → 证监会行业（有序，前者优先）。只覆盖能可靠匹配的高发主题，其余返回 None。
SECTOR_RULES: list[tuple[list[str], str]] = [
    (["软件和信息技术", "信创", "互联网", "saas", "人工智能", "算力", "ai", "大数据", "云计算", "数据要素", "数据"], "I65软件和信息技术服务业"),
    (["创新药", "医药", "生物", "药品", "医疗器械", "脑机", "药"], "C27医药制造业"),
    (["半导体", "芯片", "电子", "通信", "计算机", "数字经济", "智能"], "C39计算机、通信和其他电子设备制造业"),
    (["电力", "储能", "电网", "光伏", "风电", "核电", "火电", "水电", "充电桩", "电气", "地热"], "D44电力、热力生产和供应业"),
    (["石油", "油气", "天然气", "原油"], "B07石油和天然气开采业"),
    (["煤炭"], "B06煤炭开采和洗选业"),
    (["有色金属", "有色", "黄金", "稀土", "锂", "铜", "铝"], "C32有色金属冶炼和压延加工业"),
    (["钢铁", "钢材"], "C31黑色金属冶炼和压延加工业"),
    (["农业", "种业", "粮食", "种植", "农"], "A01农业"),
    (["养殖", "畜牧", "猪", "鸡"], "A03畜牧业"),
    (["新能源车", "新能源汽车", "汽车", "整车", "零部件", "电动车", "重卡"], "C36汽车制造业"),
    (["机器人", "工业母机", "机床", "通用设备", "机械"], "C34通用设备制造业"),
    (["专用设备", "半导体设备", "光伏设备"], "C35专用设备制造业"),
    (["军工", "国防", "航空", "航天", "船舶", "高铁", "低空"], "C37铁路、船舶、航空航天和其他运输设备制造业"),
    (["家电", "电器", "电机", "机电"], "C38电气机械和器材制造业"),
    (["白酒", "食品", "饮料", "乳"], "C14食品制造业"),
    (["纺织", "服装", "服饰", "鞋"], "C18纺织服装、服饰业"),
    (["航运", "港口", "海运", "水运"], "G55水上运输业"),
    (["航空运输", "民航", "机场"], "G56航空运输业"),
    (["铁路运输", "铁路"], "G53铁路运输业"),
    (["公路", "道路运输"], "G54道路运输业"),
    (["物流", "快递", "仓储"], "G59装卸搬运和仓储业"),
    (["房地产", "地产", "物业", "基建", "建筑", "工程"], "E47房屋建筑业"),
    (["银行", "货币", "金融"], "J66货币金融服务"),
    (["证券", "券商", "保险", "资本市场"], "J67资本市场服务"),
    (["零售", "电商", "批发", "商贸", "消费"], "F52零售业"),
    (["酒店", "旅游", "文旅", "住宿"], "H61住宿业"),
    (["餐饮", "外卖"], "H62餐饮业"),
    (["环保", "生态", "环境", "碳中和", "碳"], "N77生态保护和环境治理业"),
    (["水利", "水务"], "N76水利管理业"),
    (["教育", "职教"], "P83教育"),
    (["医疗", "医院", "卫生", "康养"], "Q84卫生"),
    (["体育", "健身"], "R89体育"),
    (["影视", "电影", "传媒", "文化", "出版"], "R87广播、电视、电影和录音制作业"),
    (["电信", "5g", "通信运营"], "I63电信、广播电视和卫星传输服务"),
    (["科研", "研发", "实验"], "M73研究和试验发展"),
]

# 弱关键词：信息量低或跨行业易误命中，子串匹配时跳过（靠多字关键词兜底）。
# - 单字泛指词（药/农/碳/乳/猪/鸡）易撞字面：如「农药」非医药、「农商行」非农业、「碳纤维」非碳中和
# - 纯英文「ai」过于宽泛（可命中任意含 ai 子串的英文/拼音）
#   —— 但「锂/铜/铝」等单字金属语义明确（均属 C32 有色金属），保留不误伤
_WEAK_KEYWORDS = {"药", "农", "碳", "乳", "猪", "鸡", "ai"}

# 板块表现回看窗口（交易日）
HORIZONS = (1, 3, 5)


def match_industry(sector_name: str) -> str | None:
    """板块名 → 证监会行业名（未命中返回 None）。

    弱关键词（单字、过于宽泛如 ai/5g）跳过，避免误命中。
    同名关键词去重后按规则顺序优先匹配。
    """
    if not sector_name:
        return None
    name = sector_name.lower().replace("/", " ").replace("（", " ").replace("）", " ").replace("(", " ").replace(")", " ")
    for keywords, industry in SECTOR_RULES:
        for kw in keywords:
            if kw.lower() in _WEAK_KEYWORDS:
                continue
            if kw in name:
                return industry
    return None


async def _load_industry_codes(industries: list[str]) -> dict[str, list[str]]:
    """行业名 → 成分股代码。"""
    if not industries:
        return {}
    async with async_session() as s:
        rows = (await s.execute(
            select(StockIndustry.industry, StockIndustry.code)
            .where(StockIndustry.industry.in_(industries),
                   StockIndustry.industry_classification == "证监会行业分类")
        )).all()
    result: dict[str, list[str]] = {}
    for ind, code in rows:
        result.setdefault(ind, []).append(code)
    return result


async def _load_stock_names(codes: set[str]) -> dict[str, str]:
    """代码(lower) → 名称（成分股 top-N 展示用）。"""
    if not codes:
        return {}
    async with async_session() as s:
        rows = (await s.execute(
            select(StockBasic.code, StockBasic.name).where(StockBasic.code.in_(codes))
        )).all()
    return {r[0]: (r[1] or "") for r in rows}


def _load_benchmark_pct(target_dates: set[date]) -> dict[date, float]:
    """阻塞读取沪深300（qlib bin）单日 pct_chg，返回 {trade_date: pct_chg}。

    qlib 未装/指数缺失时返回空 dict（前端显示 '—'），不阻塞板块表现。
    与板块口径一致：单日 pct_chg（同一 T+N 交易日）。
    """
    if not target_dates:
        return {}
    lo = (min(target_dates) - timedelta(days=2)).isoformat()
    hi = (max(target_dates) + timedelta(days=1)).isoformat()
    try:
        from app.services.quant.qlib_init import init_qlib
        init_qlib()
        from qlib.data import D

        df = D.features([BENCHMARK_CODE], ["$close"],
                        start_time=lo, end_time=hi, freq="day")
        if df is None or df.empty:
            return {}
        closes = df["$close"].astype(float)
        pct = closes.pct_change(fill_method=None) * 100
        out: dict[date, float] = {}
        for (_, dt), v in pct.items():
            d = dt.date() if hasattr(dt, "date") else dt
            if d in target_dates and v == v:  # 跳过 NaN
                out[d] = round(float(v), 2)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("读取基准指数 %s 失败: %s", BENCHMARK_CODE, e)
        return {}


def _rank_top_stocks(
    codes: list[str],
    daily: dict[str, dict[date, float]],
    horizon_map: dict[int, date | None],
    names: dict[str, str],
    n: int = TOP_STOCKS,
) -> list[dict]:
    """板块成分股按「首个可用交易日涨幅」降序，取前 N 作为龙头股。

    排序分取第一个可用 T+N 的收益（同一板块内成分股 T+N 不等，用最近可用的代表短期强弱）；
    返回 [{code, name, ret_1d, ret_3d, ret_5d}]。
    """
    ranked: list[tuple[float, dict]] = []
    for c in codes:
        d = daily.get(c.upper(), {})
        rets: dict = {"code": c.upper(), "name": names.get(c, "")}
        score = None
        for h in HORIZONS:
            td = horizon_map.get(h)
            v = d.get(td) if td is not None else None
            rets[f"ret_{h}d"] = v
            if score is None:
                score = v
        if score is not None:
            ranked.append((score, rets))
    ranked.sort(key=lambda kv: kv[0], reverse=True)
    return [r for _, r in ranked[:n]]


async def _trading_days_after(dates: set[date], max_h: int, buffer_days: int = 20) -> dict[date, dict[int, date | None]]:
    """给一组政策日，一次性拉取 (min_date, max_date+buffer] 的全部交易日，
    在内存里用 bisect 计算每天 T+1..T+max_h（不含当日），返回 {base: {h: 交易日|None}}。

    相比逐日各查一次（原 N+1），改为单次批量查询 + 内存定位。
    buffer_days 取 max_h 的实际交易日跨度余量（含节假日，20 个自然日 ⇒ ~12 个交易日，足够覆盖 max_h=5）。
    """
    if not dates:
        return {}
    start = min(dates)
    end = max(dates) + timedelta(days=buffer_days)
    async with async_session() as s:
        rows = (await s.execute(
            select(TradeCalendar.trade_date)
            .where(TradeCalendar.is_trading_day == true(),
                   TradeCalendar.trade_date > start,
                   TradeCalendar.trade_date <= end)
            .order_by(TradeCalendar.trade_date)
        )).all()
    trade_dates = [r[0] for r in rows]
    result: dict[date, dict[int, date | None]] = {}
    for base in dates:
        idx = bisect_right(trade_dates, base)
        result[base] = {}
        for h in range(1, max_h + 1):
            pos = idx + h - 1
            result[base][h] = trade_dates[pos] if pos < len(trade_dates) else None
    return result


async def compute_sector_performance(days: int = 14) -> dict:
    """最近 N 个有 AI 解读的日期，每天每个点名板块的 T+1/T+3/T+5 等权收益。

    返回:
        {"days": N, "items": [{"date": str, "sectors": [{name, direction, reason,
            industry, stocks, ret_1d, ret_3d, ret_5d}]}]}
    """
    async with async_session() as s:
        analysis_rows = (await s.execute(
            select(PolicyAnalysis.news_date, PolicyAnalysis.sectors)
            .where(PolicyAnalysis.status == "done")
            .order_by(PolicyAnalysis.news_date.desc())
            .limit(days)
        )).all()

    items: list[dict] = []
    # 需要行情的目标股票 + 交易日集合
    target_codes: set[str] = set()
    target_dates: set[date] = set()

    # 第一遍：匹配行业、收集所有政策日（一次性算交易日，避免逐日查询）
    sector_plans: list[tuple[date, list[dict]]] = []
    policy_dates: set[date] = set()
    for d, sectors in analysis_rows:
        if not sectors:
            continue
        plans: list[dict] = []
        for sec in sectors:
            if not isinstance(sec, dict) or not sec.get("name"):
                continue
            industry = match_industry(str(sec["name"]))
            plans.append({"name": str(sec["name"]), "direction": sec.get("direction") or "中性",
                          "reason": sec.get("reason"), "industry": industry})
        sector_plans.append((d, plans))
        policy_dates.add(d)

    # 第二遍：一次批量拉交易日，为每个政策日定位 T+N
    horizon_cache: dict[date, dict[int, date | None]] = {}
    if policy_dates:
        horizon_cache = await _trading_days_after(policy_dates, max(HORIZONS))
        for hmap in horizon_cache.values():
            for h in HORIZONS:
                td = hmap.get(h)
                if td is not None:
                    target_dates.add(td)

    # 第三遍：收集所有匹配行业的成分股
    industries = {p["industry"] for _, plans in sector_plans for p in plans if p["industry"]}
    ind_codes = await _load_industry_codes(sorted(industries))
    for _, plans in sector_plans:
        for p in plans:
            target_codes.update(ind_codes.get(p["industry"], []))

    # 第四遍：批量拉行情 pct_chg → {code: {trade_date: pct_chg}}
    # 注意：stock_daily.code 全大写（SH600000），stock_industry.code 全小写（sh600131），需统一转大写
    daily: dict[str, dict[date, float]] = {}
    if target_codes and target_dates:
        async with async_session() as s:
            rows = (await s.execute(
                select(StockDaily.code, StockDaily.trade_date, StockDaily.pct_chg)
                .where(StockDaily.code.in_([c.upper() for c in target_codes]),
                       StockDaily.trade_date.in_(target_dates),
                       StockDaily.pct_chg.isnot(None),
                       StockDaily.tradestatus == 1)
            )).all()
        for code, td, pct in rows:
            daily.setdefault(code, {})[td] = float(pct)

    # 第五遍：市场基准（沪深300 同交易日单日 pct_chg）+ 成分股名称（top-N 展示）
    benchmark: dict[date, float] = {}
    if target_dates:
        loop = asyncio.get_running_loop()
        benchmark = await loop.run_in_executor(None, _load_benchmark_pct, set(target_dates))
    names = await _load_stock_names(target_codes)

    # 组装
    for d, plans in sector_plans:
        horizon_map = horizon_cache[d]
        out_sectors = []
        for p in plans:
            industry = p["industry"]
            row: dict = {"name": p["name"], "direction": p["direction"], "reason": p["reason"],
                         "industry": industry}
            if industry:
                codes = ind_codes.get(industry, [])
                row["stocks"] = len(codes)
                for h in HORIZONS:
                    td = horizon_map.get(h)
                    ret = None
                    if td is not None:
                        vals = [daily[c.upper()][td] for c in codes if td in daily.get(c.upper(), {})]
                        if vals:
                            ret = round(sum(vals) / len(vals), 2)
                    row[f"ret_{h}d"] = ret
                row["top"] = _rank_top_stocks(codes, daily, horizon_map, names)
            else:
                row["stocks"] = None
                for h in HORIZONS:
                    row[f"ret_{h}d"] = None
                row["top"] = []
            out_sectors.append(row)
        # 同日基准行情（divergence 用途）：同一 T+N 交易日沪深300 的单日涨跌
        bench: dict = {}
        for h in HORIZONS:
            td = horizon_map.get(h)
            bench[f"bench_ret_{h}d"] = benchmark.get(td) if td is not None else None
        items.append({"date": d.isoformat(), "sectors": out_sectors, **bench})

    items.sort(key=lambda x: x["date"], reverse=True)
    return {"days": days, "items": items}
