"""Notification DTOs (FR-OB-56): the rules, the event types, the history,
the go-live gate and the user picker."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.domain.notifications import EVENT_KEYS

Channel = Literal["TASK", "EMAIL", "BOTH"]
Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]


def _known_event(v: str) -> str:
    if v not in EVENT_KEYS:
        raise ValueError("not a notification event type")
    return v


def _distinct(v: list) -> list:
    out = []
    for x in v:
        if (x.lower() if isinstance(x, str) else x) not in [(y.lower() if isinstance(y, str) else y) for y in out]:
            out.append(x)
    return out


Event = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_known_event)]
Users = Annotated[list[uuid.UUID], AfterValidator(_distinct)]
Emails = Annotated[list[Address], AfterValidator(_distinct)]


class NotificationRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Event
    channel: Channel
    users: Users = []
    emails: Emails = []
    active: bool = True


class NotificationRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Channel = None  # type: ignore[assignment]
    users: Users = None  # type: ignore[assignment]
    emails: Emails = None  # type: ignore[assignment]
    active: bool = None  # type: ignore[assignment]


class NotificationRuleRead(BaseModel):
    id: int
    event_type: str
    event_label: str
    kind: str          # action: always a task (FR-OB-06); notify: follows the channel
    gate: bool         # required before a channel goes LIVE
    channel: str
    users: list[str]
    emails: list[str]
    active: bool
    tested_at: datetime | None
    test_result: str | None
    version: int
    created_at: datetime
    updated_at: datetime | None


class EventTypeRead(BaseModel):
    key: str
    label: str
    kind: str
    gate: bool
    rule_id: int | None


class UserOption(BaseModel):
    id: uuid.UUID
    name: str
    email: str


class GateEventRead(BaseModel):
    event_type: str
    label: str
    rule_id: int | None
    recipients: int
    tested_at: datetime | None
    ok: bool
    problem: str | None


class RuleTestResult(BaseModel):
    rule: NotificationRuleRead
    result: str


class NotificationRead(BaseModel):
    id: int
    at: datetime
    event_type: str
    event_label: str
    problem_key: str | None
    channel: str
    recipients: list[str]
    subject: str
    outbox_id: int | None
    test: bool
    delivery: str | None = None        # an email: its outbox row's status; a task: "Task written"
    delivery_error: str | None = None
