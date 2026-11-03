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


class JobRead(BaseModel):
    """One scheduled job (per connection, and the system job)."""
    scope: str               # "connection:<id>" | "system"
    label: str
    connection_id: int | None
    interval_minutes: int
    schedule: str            # "every 5 min, Mon–Fri 06:00–22:00"
    paused: bool             # its own pause, or the master switch
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_summary: str | None
    next_run_at: datetime | None
    overdue: bool            # more than 3 intervals past its next run while it should run


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
    jobs: list[JobRead] = []
    scheduler_paused: bool = False
    system_minutes: int = 1


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
    tab: str | None = None  # the record sheet's tab the task is about (e.g. an order's "shipments")
