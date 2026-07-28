from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from app.core.database import init_db
from app.core.logging_config import setup_logging
from app.core.middleware import setup_middleware
from app.core.errors import AppError, app_error_handler, general_error_handler
from app.core.scheduler import start_scheduler, stop_scheduler
from app.api.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    setup_logging()
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
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": "2.0.0"}


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")
