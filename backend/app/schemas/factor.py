"""因子库 schema。"""
from typing import Literal
from pydantic import BaseModel, Field

# 因子类别（与前端筛选/后端 category 一致）
FactorCategory = Literal["builtin", "llm", "symbolic", "text", "automl", "alpha158"]


class FactorCreate(BaseModel):
    """新增因子请求体。"""

    name: str = Field(..., min_length=1, max_length=100, description="因子名称")
    expression: str = Field(..., min_length=1, max_length=2000, description="qlib 因子表达式")
    category: FactorCategory = Field("builtin", description="因子类别")
    description: str | None = Field(None, max_length=500, description="因子描述")
