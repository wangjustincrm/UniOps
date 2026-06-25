import uuid
from pydantic import BaseModel


class RoleResolutionRequest(BaseModel):
    role: str                              # "dept_manager" | "gm" | "opm" | "gm_or_opm" | etc.
    doc_type: str                          # "pr" | "po" | "pa"
    department_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None


class RoleResolutionResponse(BaseModel):
    resolved_user_id: uuid.UUID | None     # None = broadcast to role
    assigned_role: str
    role_label: str


class AutoSkippedStep(BaseModel):
    step_idx: int
    role: str
    comment: str = "Auto-approved (same approver holds both roles)"


class NextStepRequest(BaseModel):
    doc_type: str
    actor_id: uuid.UUID
    current_step_idx: int
    department_id: uuid.UUID | None = None


class NextStepResponse(BaseModel):
    next_step_idx: int
    auto_skipped_steps: list[AutoSkippedStep]
    is_final_step: bool
    workflow_complete: bool


class RoleManagementResponse(BaseModel):
    gm_user_id: str | None = None
    gm_backup_user_id: str | None = None
    opm_user_id: str | None = None
    opm_backup_user_id: str | None = None
    finance_manager_user_id: str | None = None
    finance_manager_backup_user_id: str | None = None
    procurement_manager_user_id: str | None = None
    procurement_manager_backup_user_id: str | None = None
    vendor_manager_user_id: str | None = None
    vendor_manager_backup_user_id: str | None = None
    finance_bp_user_ids: list[str] = []
