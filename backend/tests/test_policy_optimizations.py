"""政策风向（policy_sync / policy_ai）回归测试：并发抓取 + 重试限制。"""
import pytest

from app.services.data.policy_ai import _pending_dates
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
