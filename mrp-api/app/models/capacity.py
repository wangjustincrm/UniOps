"""User-definable capacity rules (Phase 1B Task 1, design §6.4).

A rule is `scope_type` (factory|product_family|line) x `constraint_type`
(max_sku_count|max_output_qty) x `limit_value` x effective window. Phase 1B
only creates factory-level rules (`scope_ref=None`), but the schema stays
general so product_family/line scoping can be added later without a new
migration.

DDL lives in `alembic/versions/mrp04_capacity_mps_demand.py` — Task 2 adds
three more `op.create_table` calls to that same migration file for the MPS
demand tables, so don't rename it even though this task only needs the one
table.
"""
from datetime import date

from sqlalchemy import Boolean, Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpCapacityRule(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_capacity_rules"
    scope_type: Mapped[str] = mapped_column(String(20))          # factory|product_family|line
    scope_ref: Mapped[str | None] = mapped_column(String(50))    # null for factory-wide
    constraint_type: Mapped[str] = mapped_column(String(20))     # max_sku_count|max_output_qty
    limit_value: Mapped[object] = mapped_column(Numeric(18, 3))
    uom: Mapped[str | None] = mapped_column(String(10))          # KG for max_output_qty; null for counts
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
