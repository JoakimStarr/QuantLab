from fastapi import APIRouter

from app.api.quant_data import router as quant_data_router
from app.api.factor import router as factor_router
from app.api.strategy import router as strategy_router
from app.api.mining import router as mining_router

api_router = APIRouter()

api_router.include_router(quant_data_router)
api_router.include_router(factor_router)
api_router.include_router(strategy_router)
api_router.include_router(mining_router)
