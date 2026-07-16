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
    # role_management / dept_gm_opm_mapping / dept_director_mapping /
    # dept_supervisor_enabled retired from this mirror (Task 4, approval routing
    # phase 3) — approval-api now reads its own tables via
    # app.crud.workflow.get_role_management / get_dept_gm_opm_mapping /
    # get_dept_director_mapping / get_dept_supervisor_enabled instead of this
    # EPMS-owned JSONB. Physical company_config columns still exist (epms-api
    # owns them); this class is just approval-api's read mirror.
    # Over-budget pre-approval mode (over_budget_mode) lives here — read by the
    # engine's over-budget injection block (crud/engine.py). Physical column is
    # owned by epms-api (company_config.budget_admin_config, jsonb NOT NULL).
    budget_admin_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
