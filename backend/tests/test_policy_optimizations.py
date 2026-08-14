"""政策风向（policy_sync / policy_ai）回归测试：并发抓取 + 重试限制 + 板块匹配 + API 校验。"""
from datetime import date

import pytest

from app.api.policy import _parse_date
from app.services.data.policy_ai import _pending_dates
from app.services.data.policy_sector_perf import (
    TOP_STOCKS,
    _rank_top_stocks,
    _trading_days_after,
    match_industry,
)
from app.services.data.policy_sync import fetch_policy_news


class TestFetchPolicyNewsConcurrent:
    def test_fetch_aggregates_rows_across_days(self, monkeypatch):
        """并发抓取：多日结果合并，单日失败不影响其他。"""
        def fake_cctv(date):
            if date == "20260101":
                raise RuntimeError("akshare 接口异常")
            import pandas as pd
            return pd.DataFrame([{"title": f"新闻-{date}", "content": "内容"}])

        import akshare as ak
        monkeypatch.setattr(ak, "news_cctv", fake_cctv)

        rows = fetch_policy_news(["20260101", "20260102", "20260103"])
        assert len(rows) == 2  # 失败那天跳过
        dates = {str(r["news_date"]) for r in rows}
        assert dates == {"2026-01-02", "2026-01-03"}
        assert all(r["source"] == "cctv" for r in rows)


class TestPendingDatesRetryLimit:
    async def test_pending_excludes_done_and_exhausted(self, db_ready):
        """重试限制：done 与 retry_count 达上限的 failed 日期都不再待处理。"""
        if not db_ready:
            pytest.skip("需要 Postgres")
        from datetime import date

        from app.core.database import async_session
        from app.models.policy import PolicyAnalysis, PolicyNews

        # 插入测试数据：一条 done、一条 failed 且重试耗尽、一条 failed 未耗尽、一条无解读
        async with async_session() as session:
            for d, title in [("2026-01-01", "a"), ("2026-01-02", "b"),
                             ("2026-01-03", "c"), ("2026-01-04", "d")]:
                session.add(PolicyNews(news_date=date(2026, 1, int(d[8:])), title=title, source="cctv"))
            session.add(PolicyAnalysis(news_date=date(2026, 1, 1), status="done"))
            session.add(PolicyAnalysis(news_date=date(2026, 1, 2), status="failed", retry_count=3))
            session.add(PolicyAnalysis(news_date=date(2026, 1, 3), status="failed", retry_count=1))
            await session.commit()

        pending = await _pending_dates(max_days=365)
        dates = [d.isoformat() for d in pending]
        # done(01)、耗尽(02) 排除；failed 未耗尽(03) 与无解读(04) 保留
        assert "2026-01-01" not in dates
        assert "2026-01-02" not in dates
        assert "2026-01-03" in dates
        assert "2026-01-04" in dates
        # 倒序（新→旧）
        assert dates == sorted(dates, reverse=True)

        # 清理测试数据
        async with async_session() as session:
            await session.execute(PolicyNews.__table__.delete().where(
                PolicyNews.news_date >= date(2026, 1, 1),
                PolicyNews.news_date <= date(2026, 1, 4)))
            await session.execute(PolicyAnalysis.__table__.delete().where(
                PolicyAnalysis.news_date >= date(2026, 1, 1),
                PolicyAnalysis.news_date <= date(2026, 1, 4)))
            await session.commit()


class TestMatchIndustryWeakKeywords:
    """板块名 → 证监会行业匹配：单字/宽泛弱关键词不误命中。"""

    def test_multi_char_keyword_still_matches(self):
        assert match_industry("AI算力") == "I65软件和信息技术服务业"
        assert match_industry("新能源车") == "C36汽车制造业"
        assert match_industry("白酒") == "C14食品制造业"

    def test_weak_single_char_keyword_skipped(self):
        # 「药」被跳过：报价药房不属于医药制造，命中多字词「医药」才算
        assert match_industry("药房") is None
        # 「农」被跳过：农商行不含「银行」也不含其它金融关键词 → 宁可无对照也不误判农业
        assert match_industry("农商行") is None
        # 含多字金融关键词（银行）时正常命中
        assert match_industry("农商银行") == "J66货币金融服务"

    def test_weak_english_ai_skipped(self):
        # 「ai」宽泛易误命中的英文子串应被跳过
        assert match_industry("mainland banking") is None
        # 多字英文关键词仍生效
        assert match_industry("saas 平台") == "I65软件和信息技术服务业"

    def test_empty_returns_none(self):
        assert match_industry("") is None
        assert match_industry(None) is None
        assert match_industry(" ") is None


class TestTradingDaysAfter:
    """批量交易日定位：单次查询 + bisect，定位 T+1/T+3/T+5。"""

    async def test_horizons_computed_in_memory(self, db_ready):
        if not db_ready:
            pytest.skip("需要 Postgres")
        import datetime

        from app.core.database import async_session
        from app.models.baostock import TradeCalendar

        # 构造连续工作日（2026-01-05 周一 ~ 01-09 周五），无视周末
        days = [datetime.date(2026, 1, n) for n in range(5, 10)]
        async with async_session() as s:
            for d in days:
                s.add(TradeCalendar(trade_date=d, is_trading_day=True))
            await s.commit()

        result = await _trading_days_after({datetime.date(2026, 1, 6)}, max_h=3)
        hmap = result[datetime.date(2026, 1, 6)]
        assert hmap[1] == datetime.date(2026, 1, 7)
        assert hmap[2] == datetime.date(2026, 1, 8)
        assert hmap[3] == datetime.date(2026, 1, 9)

        # 清理
        async with async_session() as s:
            await s.execute(TradeCalendar.__table__.delete().where(
                TradeCalendar.trade_date >= datetime.date(2026, 1, 1),
                TradeCalendar.trade_date <= datetime.date(2026, 1, 31)))
            await s.commit()

    async def test_missing_horizon_returns_none(self, db_ready):
        if not db_ready:
            pytest.skip("需要 Postgres")
        import datetime

        from app.core.database import async_session
        from app.models.baostock import TradeCalendar

        # 只有一天交易日，T+5 应为 None
        async with async_session() as s:
            s.add(TradeCalendar(trade_date=datetime.date(2026, 2, 3), is_trading_day=True))
            await s.commit()

        result = await _trading_days_after({datetime.date(2026, 2, 2)}, max_h=5)
        hmap = result[datetime.date(2026, 2, 2)]
        assert hmap[1] is not None
        assert hmap[5] is None

        async with async_session() as s:
            await s.execute(TradeCalendar.__table__.delete().where(
                TradeCalendar.trade_date >= datetime.date(2026, 2, 1),
                TradeCalendar.trade_date <= datetime.date(2026, 2, 28)))
            await s.commit()


class TestParseDateValidation:
    """API 日期参数：非法输入返回 400 而非 500。"""

    def test_valid_date(self):
        assert _parse_date("2026-01-01", "start") == date(2026, 1, 1)

    def test_none_returns_none(self):
        assert _parse_date(None, "start") is None

    def test_invalid_raises_http_400(self):
        import fastapi

        for bad in ("2026/01/01", "01-01-2026", "abc", "2026-13-01"):
            with pytest.raises(fastapi.HTTPException) as exc:
                _parse_date(bad, "start")
            assert exc.value.status_code == 400
            assert "日期格式非法" in str(exc.value.detail)


class TestRankTopStocks:
    """龙头股排序：按首个可用交易日涨幅降序，取前 N。"""

    t1 = date(2026, 3, 3)
    t3 = date(2026, 3, 5)

    def _daily(self, data: dict[str, dict[date, float]]):
        return {c.upper(): d for c, d in data.items()}

    def test_sorted_by_first_available_horizon(self):
        codes = ["sh600001", "sh600002", "sh600003"]
        daily = self._daily({
            "sh600001": {self.t1: 5.0, self.t3: 1.0},
            "sh600002": {self.t1: -2.0},
            "sh600003": {self.t3: 9.0},  # T+1 无数据 → 用 T+3 作排序分
        })
        horizon_map = {1: self.t1, 3: self.t3, 5: None}
        top = _rank_top_stocks(codes, daily, horizon_map, {})
        assert [t["code"] for t in top] == ["SH600003", "SH600001", "SH600002"]
        assert top[0]["ret_3d"] == 9.0

    def test_respects_n_and_skips_no_data(self):
        codes = [f"sh60{n}001" for n in range(20)]
        daily = self._daily({c: {self.t1: float(i)} for i, c in enumerate(codes)})
        top = _rank_top_stocks(codes, daily, {1: self.t1, 3: self.t3, 5: None}, {})
        assert len(top) == TOP_STOCKS
        assert top[0]["code"] == "SH6019001"  # 最高涨幅的代码
        # 无任何行情数据的代码被剔除
        empty = _rank_top_stocks(["sh600099"], {}, {1: self.t1, 3: self.t3, 5: None}, {})
        assert empty == []

    def test_names_joined(self):
        codes = ["sh600001"]
        daily = self._daily({"sh600001": {self.t1: 1.0}})
        top = _rank_top_stocks(codes, daily, {1: self.t1, 3: self.t3, 5: None},
                               {"sh600001": "浦发银行"})
        assert top[0]["name"] == "浦发银行"

    def test_horizon_none_dates_dropped(self):
        codes = ["sh600001"]
        daily = self._daily({"sh600001": {self.t1: 1.0}})
        # T+N 均为 None（无交易日）→ 无数据可选，返回空
        top = _rank_top_stocks(codes, daily, {1: None, 3: None, 5: None}, {})
        assert top == []
