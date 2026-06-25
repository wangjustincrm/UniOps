"""Pydantic schemas for VmsConfig (singleton, admin-managed)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class NotificationContacts(BaseModel):
    """Shape of vms_config.notification_contacts JSONB (PRD §2.1.2.1)."""
    training_email: EmailStr | None = None
    ppe_email: EmailStr | None = None


class VmsConfigResponse(BaseModel):
    """Returned by GET /admin/config — full snapshot of the singleton."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    quality_manager_user_ids: list[uuid.UUID]
    notification_contacts: NotificationContacts
    health_questions: dict
    badge_templates: dict
    updated_at: datetime
    updated_by: uuid.UUID | None


class QualityManagerRosterUpdate(BaseModel):
    """Replaces the full VMS-local Quality Manager roster atomically
    (PUT /admin/quality-managers)."""
    user_ids: list[uuid.UUID] = Field(default_factory=list)


class NotificationContactsUpdate(BaseModel):
    """Replaces notification_contacts (PUT /admin/notification-contacts).

    Either field can be `None` to clear it; sending the whole object replaces
    the existing one.
    """
    training_email: EmailStr | None = None
    ppe_email: EmailStr | None = None


# ── Health questionnaire template ───────────────────────────────────────────-

class HealthQuestionEntry(BaseModel):
    """One row in the health questionnaire template."""
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    fail_on: str = Field(default="yes", max_length=20)


class HealthQuestionsUpdate(BaseModel):
    """Replaces `vms_config.health_questions` atomically.

    Question `id`s must be unique within a template — the audit log relies
    on them as stable identifiers when answers are baked into declarations.
    """
    version: int = Field(default=1, ge=1)
    questions: list[HealthQuestionEntry] = Field(default_factory=list)


# ── SMTP settings ───────────────────────────────────────────────────────────-

class SmtpSettingsUpdate(BaseModel):
    """VMS outbound SMTP credentials.

    All fields optional — an entirely empty payload (or just `host=None`)
    causes vms-api to fall back to the shared `company_config` row used by
    EPMS / OA. Password isn't EmailStr (server URLs etc), and `from_email`
    accepts EmailStr only — that's the address recipients see.
    """
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    use_tls: bool | None = None
    from_email: EmailStr | None = None


class SmtpTestRequest(BaseModel):
    """POST /admin/smtp-test — send one test email using the current settings."""
    to_email: EmailStr


class SmtpTestResponse(BaseModel):
    delivered: bool
    detail: str
