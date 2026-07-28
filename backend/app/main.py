from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from app.core.database import init_db
from app.core.logging_config import setup_logging
from app.core.middleware import setup_middleware
from app.core.errors import AppError, app_error_handler, general_error_handler
from app.core.scheduler import start_scheduler, stop_scheduler
from app.api.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    await start_scheduler()
    yield
    await stop_scheduler()


app = FastAPI(title="QuantLab", version="2.0.0", lifespan=lifespan)

setup_middleware(app)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, general_error_handler)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health():
    status = "ok"
    checks = {}
    # 数据库检查
    try:
        from app.core.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        status = "degraded"
    # qlib 检查
    try:
        from app.services.quant.qlib_init import is_qlib_available
        qlib_ok = await is_qlib_available()
        checks["qlib"] = "ok" if qlib_ok else "not_available"
    except Exception as e:
        checks["qlib"] = f"error: {e}"
    return {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "checks": checks,
    }


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")
