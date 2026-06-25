"""Read-only mirror of EPMS CompanyConfig — only fields needed by the Approval Engine."""
import uuid
from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class CompanyConfig(Base):
    __tablename__ = "company_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workflow_defs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    role_management: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dept_gm_opm_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Over-budget pre-approval mode (over_budget_mode) lives here — read by the
    # engine's over-budget injection block (crud/engine.py). Physical column is
    # owned by epms-api (company_config.budget_admin_config, jsonb NOT NULL).
    budget_admin_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
