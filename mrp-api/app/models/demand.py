"""MRP demand input (Phase 1B Task 2, design §6.4).

DDL lives in `alembic/versions/mrp04_capacity_mps_demand.py` alongside
`mrp_capacity_rules` (Task 1) and `mrp_mps_runs`/`mrp_mps_lines`
(`app/models/mps.py`, this same task) — don't rename that migration.
"""
import uuid

from sqlalchemy import CHAR, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpDemand(Base, UUIDPrimaryKey, TimestampMixin):
    """MRP input, materialized from a released MPS run (design §6.4: mrp_demands
    is no longer hand-imported). Phase 1C's material explosion reads this."""
    __tablename__ = "mrp_demands"
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    demand_type: Mapped[str] = mapped_column(String(20), default="mps", server_default="mps")
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    demand_month: Mapped[str] = mapped_column(CHAR(7), index=True)
    qty: Mapped[object] = mapped_column(Numeric(18, 3))
