"""Pydantic schemas for user management (admin)."""
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


VALID_ROLES = {
    "system_admin", "gm", "opm", "finance_manager", "finance_bp",
    "ap_clerk", "requester", "dept_manager",
    "procurement_officer", "procurement_manager",
    "warehouse_staff", "cfo", "auditor", "vendor_manager",
}


VALID_NOTIFICATION_CHANNELS = {"email_only", "teams_only", "both", "none"}


class UserCreate(BaseModel):
    """Admin creates a new user with an explicit password."""
    email: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8)
    role: str = "requester"
    department_id: uuid.UUID | None = None
    is_active: bool = True
    teams_account: str | None = None
    notification_channel: str = "email_only"
    must_change_password: bool = True   # admin-created users must change on first login
    # ERP person code. Optional at the schema level (CSV import omits it); the
    # admin "Add User" endpoint enforces it as required + unique.
    erp_person_code: str | None = Field(default=None, max_length=50)


class UserUpdate(BaseModel):
    """Fields an admin can update on any user."""
    email: str | None = Field(default=None, min_length=1, max_length=255)
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = None
    department_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)
    teams_account: str | None = None
    notification_channel: str | None = None
    # Settable only while currently empty; read-only once a code exists
    # (ERP-imported or previously set). Uniqueness enforced in the endpoint.
    erp_person_code: str | None = Field(default=None, max_length=50)


class UserAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    department_id: uuid.UUID | None
    department_name: str | None = None
    is_active: bool
    mfa_enabled: bool
    teams_account: str | None = None
    notification_channel: str = "email_only"
    must_change_password: bool = False
    erp_person_code: str | None = None
    erp_imported: bool = False


class UserListResponse(BaseModel):
    items: list[UserAdminResponse]
    total: int


class UserListItem(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    department_id: uuid.UUID | None
    is_active: bool
    mfa_enabled: bool

    model_config = {"from_attributes": True}


# ── Public directory (open to any authenticated user) ────────────────────────
# Used by VMS Host search and any other module that needs a lightweight
# "who is this person" lookup without exposing admin-only fields (role,
# is_active flag, mfa state, password hash, etc.).

class UserBriefResponse(BaseModel):
    """Minimal user info safe to expose to any authenticated UniOps user."""
    id: uuid.UUID
    full_name: str
    email: str
    department_id: uuid.UUID | None = None
    department_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class UserBriefListResponse(BaseModel):
    items: list[UserBriefResponse]
    total: int


class ErpImportItem(BaseModel):
    erp_person_code: str
    email: str
    role: str | None = None
    department_id: uuid.UUID | None = None
    is_active: bool = True

    @field_validator("email")
    @classmethod
    def _email_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("email is required")
        return v


class ErpImportRequest(BaseModel):
    items: list[ErpImportItem]


class ErpImportCreated(BaseModel):
    email: str
    full_name: str
    temp_password: str


class ErpImportError(BaseModel):
    erp_person_code: str
    reason: str


class ErpImportResponse(BaseModel):
    created: list[ErpImportCreated]
    errors: list[ErpImportError]
