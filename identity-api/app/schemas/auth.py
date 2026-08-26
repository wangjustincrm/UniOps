"""Pydantic schemas for auth endpoints (contract-identical to epms-api's)."""
import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)
    role: str = "requester"
    department_id: uuid.UUID | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class ResendOtpRequest(BaseModel):
    mfa_token: str


class MfaChallengeRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=6)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class MfaRequiredResponse(BaseModel):
    mfa_required: Literal[True] = True
    mfa_token: str


# A signature drawn on the pad exports to roughly 5-30 KB of base64; an
# uploaded photo can be far larger. Cap it server-side so nobody can park a
# multi-megabyte image on a row that the PO PDF renderer loads synchronously.
MAX_SIGNATURE_CHARS = 400_000


class UpdateMeRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    teams_account: str | None = None
    notification_channel: str | None = None
    # None = leave unchanged; "" = clear the stored signature. Without the
    # empty-string path there would be no way to remove a signature once set.
    signature_image: str | None = Field(default=None, max_length=MAX_SIGNATURE_CHARS)

    @field_validator("signature_image")
    @classmethod
    def _data_url_only(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        if not v.startswith("data:image/"):
            raise ValueError("signature_image must be a data:image/... base64 URL")
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    department_id: uuid.UUID | None
    is_active: bool
    mfa_enabled: bool
    must_change_password: bool = False
    teams_account: str | None = None
    notification_channel: str = "email_only"
    signature_image: str | None = None

    model_config = {"from_attributes": True}
