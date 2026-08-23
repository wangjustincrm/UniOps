"""User-definable capacity rules (Phase 1B Task 1, design §6.4; weekly
minimum-output constraint + per-week exceptions added by the weekly-MPS
Task 3).

A rule is `scope_type` (factory|product_family|line) x `constraint_type`
(max_sku_count|max_output_qty|min_output_qty) x `limit_value` x effective
window. Phase 1B only creates factory-level rules (`scope_ref=None`), but
the schema stays general so product_family/line scoping can be added later
without a new migration. `min_output_qty` is a SOFT floor — see
`app/services/capacity.py::resolve_limits_for_week`'s docstring — it must
never be treated as a hard ceiling the way the other two constraint types
are.

DDL lives in `alembic/versions/mrp04_capacity_mps_demand.py` — Task 2 adds
three more `op.create_table` calls to that same migration file for the MPS
demand tables, so don't rename it even though this task only needs the one
table.
"""
from datetime import date

from sqlalchemy import Boolean, Date, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpCapacityRule(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_capacity_rules"
    scope_type: Mapped[str] = mapped_column(String(20))          # factory|product_family|line
    scope_ref: Mapped[str | None] = mapped_column(String(50))    # null for factory-wide
    constraint_type: Mapped[str] = mapped_column(String(20))     # max_sku_count|max_output_qty|min_output_qty
    limit_value: Mapped[object] = mapped_column(Numeric(18, 3))
    uom: Mapped[str | None] = mapped_column(String(10))          # KG for *_output_qty; null for counts
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class MrpCapacityException(Base, UUIDPrimaryKey):
    """Per-week override of a standing `MrpCapacityRule` value — e.g. a
    shutdown week's `max_output_qty=0`. DDL lives in
    `alembic/versions/mrp10a_planning_params.py` (weekly-MPS Task 2); this
    is Task 3's ORM model over that already-migrated table — see that
    migration's module docstring, which explicitly reserves this model for
    Task 3 and warns not to re-create the table.

    No `TimestampMixin`: the migration does not give this table
    `created_at`/`updated_at` columns (unlike `MrpCapacityRule`).

    Two DB-level uniqueness guarantees this model relies on and does not
    re-implement in Python: a plain unique constraint on `(week_start,
    scope_type, scope_ref, constraint_type)`, plus a partial unique index on
    `(week_start, scope_type, constraint_type) WHERE scope_ref IS NULL` —
    Postgres treats every NULL as distinct, so factory-wide rows
    (`scope_ref=None`) need the extra partial index to actually dedupe.
    Together they mean at most one row (active or not) can ever exist for a
    given (week, scope, constraint_type); `resolve_limits_for_week` leans on
    that when it queries active exceptions for a week without needing its
    own tie-break.
    """
    __tablename__ = "mrp_capacity_exceptions"
    week_start: Mapped[date] = mapped_column(Date)
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_ref: Mapped[str | None] = mapped_column(String(50))
    constraint_type: Mapped[str] = mapped_column(String(20))     # max_sku_count|max_output_qty|min_output_qty
    limit_value: Mapped[object] = mapped_column(Numeric(18, 3))
    uom: Mapped[str | None] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
