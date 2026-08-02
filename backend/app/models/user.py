"""用户模型（fastapi-users 标准 SQLAlchemy 实现）。"""
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class User(SQLAlchemyBaseUserTable[int], Base):
    """用户模型。

    继承 fastapi-users 的 SQLAlchemyBaseUserTable，显式定义 id 主键。
    SQLAlchemyBaseUserTable 已提供 email, hashed_password, is_active,
    is_superuser, is_verified 字段。
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
