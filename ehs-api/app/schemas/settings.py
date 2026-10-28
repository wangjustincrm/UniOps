"""Settings: controlled vocabularies, behaviour parameters, the plant tree."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # Left out: derived from the label (crud.vocabulary._derive_code).
    code: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_\-]+$")
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
    # Labels from the top down, " › "-joined — what a record snapshots.
    trail: str | None = None


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capa_remind_before_days: int
    capa_escalate_supervisor_days: int
    capa_escalate_manager_days: int
    cert_warn_days: list[int]
    allow_anonymous_report: bool
    statutory_scan_interval_minutes: int
    incident_notify_groups: dict
    incident_status_notify: dict = Field(default_factory=dict)
    incident_notify_include: dict = Field(default_factory=dict)
    jhsc_answered_notify: dict = Field(default_factory=lambda: {"people": [], "sender": True})
    email_templates: dict
    lost_time_rules: dict
    # Null until somebody picks one — every meeting before that used the
    # free-text box, and still does.
    jhsc_minutes_template_code: str | None
    # What an in-house certificate says. The plant's own wording; who earned it
    # and when are not configurable, because those are what make it evidence.
    certificate_heading: str | None
    certificate_body: str | None
    certificate_signatory: str | None


class ConfigUpdate(BaseModel):
    capa_remind_before_days: int | None = Field(default=None, ge=0, le=90)
    capa_escalate_supervisor_days: int | None = Field(default=None, ge=1, le=180)
    capa_escalate_manager_days: int | None = Field(default=None, ge=1, le=365)
    cert_warn_days: list[int] | None = None
    allow_anonymous_report: bool | None = None
    # 0 disables the sweep. Capped at a day so a typo cannot park it for a year.
    statutory_scan_interval_minutes: int | None = Field(default=None, ge=0, le=1440)
    incident_notify_groups: dict | None = None
    incident_status_notify: dict | None = None
    incident_notify_include: dict | None = None
    jhsc_answered_notify: dict | None = None
    email_templates: dict | None = None
    lost_time_rules: dict | None = None
    # The form the JHSC minutes are written on. Empty string clears it back to
    # the free-text box; null means "not mentioned in this request".
    jhsc_minutes_template_code: str | None = Field(default=None, max_length=40)
    certificate_heading: str | None = Field(default=None, max_length=120)
    certificate_body: str | None = None
    certificate_signatory: str | None = Field(default=None, max_length=150)

    @field_validator("incident_status_notify")
    @classmethod
    def _known_events_and_people(cls, v):
        """Only the changes the notifier sends for, and only real user ids.

        A misspelt event would be stored, shown as saved, and never fire —
        which is the shape of fault this screen exists to remove.
        """
        if v is None:
            return v
        from app.services.notify import STATUS_EVENTS
        out: dict[str, list[str]] = {}
        for event, ids in v.items():
            if event not in STATUS_EVENTS:
                raise ValueError(
                    f"'{event}' is not a status change. One of: {', '.join(STATUS_EVENTS)}")
            if not isinstance(ids, list):
                raise ValueError(f"The list for '{event}' must be a list of people")
            out[event] = [str(uuid.UUID(str(i))) for i in dict.fromkeys(ids)]
        return out

    @field_validator("jhsc_answered_notify")
    @classmethod
    def _people_and_the_sender(cls, v):
        """Round 9: real user ids, and one flag."""
        if v is None:
            return v
        if not isinstance(v, dict) or set(v) - {"people", "sender"}:
            raise ValueError("The JHSC list takes people and sender, nothing else")
        people = v.get("people") or []
        if not isinstance(people, list):
            raise ValueError("people must be a list")
        return {"people": [str(uuid.UUID(str(i))) for i in dict.fromkeys(people)],
                "sender": bool(v.get("sender", True))}

    @field_validator("incident_notify_include")
    @classmethod
    def _known_lists_and_flags(cls, v):
        """Only lists the notifier reads, and only the two flags it knows."""
        if v is None:
            return v
        from app.services.incident_type import BY_CODE
        from app.services.notify import STATUS_EVENTS
        known = {"*", *BY_CODE, *(f"status:{e}" for e in STATUS_EVENTS)}
        out: dict[str, dict[str, bool]] = {}
        for key, flags in v.items():
            if key not in known:
                raise ValueError(f"'{key}' is not a list on Who is told")
            if not isinstance(flags, dict) or set(flags) - {"reporter", "supervisor"}:
                raise ValueError(f"The list '{key}' takes reporter and supervisor, nothing else")
            out[key] = {"reporter": bool(flags.get("reporter")),
                        "supervisor": bool(flags.get("supervisor"))}
        return out

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
    # False on a node that is only a heading — "Production" groups the
    # operational areas and is not itself somewhere an incident happens.
    is_selectable: bool = True
    sort_order: int = 0
    children: list[LocationNode] = Field(default_factory=list)
