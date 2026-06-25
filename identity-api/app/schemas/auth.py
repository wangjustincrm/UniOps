"""Pydantic schemas for auth endpoints (contract-identical to epms-api's)."""
import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


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


class UpdateMeRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    teams_account: str | None = None
    notification_channel: str | None = None


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

    model_config = {"from_attributes": True}
