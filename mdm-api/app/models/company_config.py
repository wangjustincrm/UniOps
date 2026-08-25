"""company_config 的部分镜像 —— 这张表由 epms-api 的 alembic 拥有。

mdm-api 只需要一列:ERP 主数据同步的间隔。照 finance-api/app/models/mirrors.py
的做法,只声明用得着的列,其余留给拥有者。

⚠️ 绝不要把这张表写进 mdm-api 自己的 alembic —— 它不归这个服务管。
测试库靠 Base.metadata.create_all 建表,所以本模型必须注册进 tests/conftest.py。
"""
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class CompanyConfig(UUIDPrimaryKey, Base):
    __tablename__ = "company_config"

    # NULL=没人设过(回落 DEFAULT_INTERVAL_MINUTES) / 0=关闭 / >0=分钟数
    erp_mdm_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
