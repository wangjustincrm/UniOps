"""Read-only mirror of EPMS company_config — used for GM/OPM department mapping."""
import uuid

from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EpmsCompanyConfig(Base):
    """Minimal read-only mirror of EPMS company_config table."""
    __tablename__ = "company_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    dept_gm_opm_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # action_key → ordered list of steps [{id, role, label}]; drives OA approval
    # participation visibility (req 1) and the claim approval-status list (req 2).
    workflow_defs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Role → user assignments (finance_bp_user_ids, finance_manager_user_id, gm/opm, …).
    # Resolves role-based approval tasks to concrete users.
    role_management: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
