"""项目配置端点：暴露版本等运行时配置给前端。"""
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config():
    """获取项目运行时配置（版本、名称、构建信息等）。

    前端通过此端点获取统一版本号，避免前后端版本不一致。"""
    return {
        "name": getattr(settings, "app_name", "QuantLab"),
        "version": getattr(settings, "app_version", "0.0.0"),
        "description": getattr(settings, "app_description", ""),
        "timezone": getattr(settings, "app_timezone", "Asia/Shanghai"),
        "api_version": "v1",
    }
