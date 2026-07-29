from fastapi import APIRouter

from app.api.quant_data import router as quant_data_router
from app.api.factor import router as factor_router
from app.api.strategy import router as strategy_router
from app.api.mining import router as mining_router
from app.api.logs import router as logs_router
from app.api.auth import router as auth_router
from app.api.data_ext import router as data_ext_router
from app.api.factor_ext import router as factor_ext_router
from app.api.strategy_ext import router as strategy_ext_router
from app.api.mining_ext import router as mining_ext_router
from app.api.market import router as market_router

api_router = APIRouter()

api_router.include_router(auth_router)
# ext routers must be registered BEFORE base routers
# to avoid /{strategy_id} catching /backtest-statuses etc.
api_router.include_router(data_ext_router)
api_router.include_router(factor_ext_router)
api_router.include_router(strategy_ext_router)
api_router.include_router(mining_ext_router)
api_router.include_router(quant_data_router)
api_router.include_router(factor_router)
api_router.include_router(strategy_router)
api_router.include_router(mining_router)
api_router.include_router(logs_router)
api_router.include_router(market_router)
