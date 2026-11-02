"""Operations Health and dashboard DTOs (FR-OB-52 / 52a / 52b)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.notifications import GateEventRead


class CheckRead(BaseModel):
    key: str
    label: str
    result: str          # PASSED | WARNING | FAILED
    detail: str
    count: int | None = None
    # The list behind it: {page, resource?, filters?}.
    link: dict[str, Any] | None = None


class HealthRead(BaseModel):
    result: str
    checked_at: datetime
    checks: list[CheckRead]
    # critical, warnings, failed_inbound, failed_outbound, waiting_ack, rejected_ack, ack_timeout,
    # exceptions_944, exceptions_945, transfer_discrepancies (FR-OB-52a)
    cards: dict[str, int]
    shadow_mode: bool
    partner_writes_enabled: bool
    external_email_enabled: bool
    scheduler_enabled: bool
    gate: list[GateEventRead]
    gate_ok: bool


class CardRead(BaseModel):
    key: str
    label: str
    count: int
    link: dict[str, Any]
    parts: list[CardRead] = []


class TaskCount(BaseModel):
    type: str
    count: int


class RunBrief(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None


class DashboardRead(BaseModel):
    cards: list[CardRead]
    open_tasks: list[TaskCount]
    needs_review: int
    waiting_delivery: int
    runs: list[RunBrief]


class InboxTarget(BaseModel):
    """Where a Sales task opens: the page, and the record (its list's base
    path and id) to open there."""
    page: str
    resource: str | None
    id: int | None
