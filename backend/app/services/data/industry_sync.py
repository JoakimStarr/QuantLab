"""申万行业分类数据同步"""
import logging
import json
import os

from app.core.config import settings
from app.services.data.code_utils import to_qlib_code

logger = logging.getLogger(__name__)

INDUSTRY_MAP_PATH = settings.PROJECT_ROOT / "data" / "industry_map.json"


def sync_industry_data() -> dict:
    """通过 akshare 获取申万一级行业分类

    使用 sw_index_first_info() 获取申万一级行业列表，
    再用 index_component_sw() 获取各行业成分股。

    Returns:
        {"ok": True, "industries": N, "stocks": M}
    """
    try:
        import akshare as ak
        # 获取申万一级行业列表
        sw_industries = ak.sw_index_first_info()
        if sw_industries is None or sw_industries.empty:
            return {"ok": False, "error": "获取申万行业列表失败"}

        # 列名：行业代码(801010.SI), 行业名称, 成份个数, ...
        logger.info("申万一级行业: %d 个", len(sw_industries))

        industry_map = {}  # {stock_code: industry_name}

        for _, row in sw_industries.iterrows():
            industry_code = str(row.iloc[0])  # 行业代码 801010.SI
            industry_name = str(row.iloc[1])  # 行业名称

            # index_component_sw 需要 6 位代码（去掉 .SI 后缀）
            sw_code = industry_code.split(".")[0]

            try:
                cons = ak.index_component_sw(symbol=sw_code)
                if cons is None or cons.empty:
                    logger.warning("行业 %s(%s) 无成分股", industry_name, sw_code)
                    continue

                # 列名：序号, 证券代码, 证券名称, 最新权重, 计入日期
                for _, stock_row in cons.iterrows():
                    code = str(stock_row.iloc[1]).zfill(6)  # 证券代码
                    qlib_code = to_qlib_code(code)
                    industry_map[qlib_code] = industry_name

                logger.info("行业 %s: %d 只股票", industry_name, len(cons))
            except Exception as e:
                logger.warning("获取行业 %s(%s) 成分股失败: %s", industry_name, sw_code, e)
                continue

        # 保存
        os.makedirs(os.path.dirname(INDUSTRY_MAP_PATH), exist_ok=True)
        with open(INDUSTRY_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(industry_map, f, ensure_ascii=False, indent=2)

        logger.info("行业分类同步完成: %d 个行业, %d 只股票",
                    len(set(industry_map.values())), len(industry_map))

        return {
            "ok": True,
            "industries": len(set(industry_map.values())),
            "stocks": len(industry_map),
        }
    except Exception as e:
        logger.error("行业分类同步失败: %s", e)
        return {"ok": False, "error": str(e)}


def load_industry_map() -> dict:
    """加载行业映射"""
    if not os.path.exists(INDUSTRY_MAP_PATH):
        return {}
    with open(INDUSTRY_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


async def load_industry_map_async() -> dict:
    """从 PG stock_industry 加载行业映射（优先），JSON 文件兜底。"""
    try:
        from sqlalchemy import select
        from app.core.database import async_session
        from app.models.baostock import StockIndustry
        async with async_session() as session:
            result = await session.execute(select(StockIndustry.code, StockIndustry.industry))
            rows = result.all()
            if rows:
                return {code: ind for code, ind in rows if ind}
    except Exception as e:
        logger.warning("PG 行业映射加载失败，回退 JSON: %s", e)
    return load_industry_map()
