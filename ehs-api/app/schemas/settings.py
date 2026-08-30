"""Settings: controlled vocabularies, behaviour parameters, the plant tree."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VocabularyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None
    is_hierarchical: bool
    # Locked lists can be relabelled but not added to or removed from: the
    # hierarchy of controls is an international standard, and the injury
    # classes drive statutory reporting and the injury rates.
    is_system_locked: bool
    item_count: int = 0
    active_item_count: int = 0


class VocabularyItemIn(BaseModel):
    code: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_\-]+$")
    label: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    sort_order: int = 0
    attrs: dict = Field(default_factory=dict)


class VocabularyItemPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None
    # Retiring an entry, which is the only form of removal there is.
    is_active: bool | None = None
    attrs: dict | None = None

    @model_validator(mode="after")
    def _something_to_do(self):
        if all(getattr(self, f) is None for f in
               ("label", "parent_id", "sort_order", "is_active", "attrs")):
            raise ValueError("nothing to update")
        return self


class VocabularyItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vocabulary_code: str
    parent_id: uuid.UUID | None
    code: str
    label: str
    sort_order: int
    is_active: bool
    attrs: dict
    path: str | None


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capa_remind_before_days: int
    capa_escalate_supervisor_days: int
    capa_escalate_manager_days: int
    cert_warn_days: list[int]
    allow_anonymous_report: bool
    statutory_scan_interval_minutes: int
    incident_notify_groups: dict
    email_templates: dict
    lost_time_rules: dict


class ConfigUpdate(BaseModel):
    capa_remind_before_days: int | None = Field(default=None, ge=0, le=90)
    capa_escalate_supervisor_days: int | None = Field(default=None, ge=1, le=180)
    capa_escalate_manager_days: int | None = Field(default=None, ge=1, le=365)
    cert_warn_days: list[int] | None = None
    allow_anonymous_report: bool | None = None
    # 0 disables the sweep. Capped at a day so a typo cannot park it for a year.
    statutory_scan_interval_minutes: int | None = Field(default=None, ge=0, le=1440)
    incident_notify_groups: dict | None = None
    email_templates: dict | None = None
    lost_time_rules: dict | None = None

    @model_validator(mode="after")
    def _ladder_must_ascend(self):
        sup, mgr = self.capa_escalate_supervisor_days, self.capa_escalate_manager_days
        if sup is not None and mgr is not None and mgr <= sup:
            raise ValueError(
                "the HSE Manager threshold must be later than the supervisor "
                "threshold, or the ladder skips a rung"
            )
        return self


class LocationNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    level: str
    path: str
    depth: int
    access_area: str | None
    qr_token: str | None
    is_active: bool
    children: list[LocationNode] = Field(default_factory=list)
