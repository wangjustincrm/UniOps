"""NC → UniOps cost-center mapping (account-aware).

Imported from finance's `Budget vs Actual Mapping.xlsx`; replaces the hardcoded
CC_BY_CODE / CC_BY_DEPT dicts in nc_sync. Key = (account_code, dept_code,
nc_cc_code) with 'ALL' as a wildcard on dept / nc_cc. A miss means the combo is
not a valid budget bucket (e.g. engineering dept in 6602) → surfaces as an
exception in the predreal grid.
"""
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class BudgetActualCcMap(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_actual_cc_map"
    __table_args__ = (
        UniqueConstraint("account_code", "dept_code", "nc_cc_code", name="uq_ba_cc_map"),
    )

    account_code: Mapped[str] = mapped_column(String(10), nullable=False)   # 5101/5301/6601/6602/6603
    dept_code: Mapped[str] = mapped_column(String(20), nullable=False)      # 'ALL' wildcard
    nc_cc_code: Mapped[str] = mapped_column(String(20), nullable=False)     # 'ALL' wildcard
    uniops_cc_code: Mapped[str] = mapped_column(String(50), nullable=False) # -> cost_centers.code
