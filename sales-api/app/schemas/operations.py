"""Read DTOs of the document register, the runs and the log. The document's
bytes are never in a list or a detail; they have their own download."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EntityRef(BaseModel):
    """A record a row points at: what to open and what to call it."""
    id: int
    label: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    direction: str
    origin: str
    system: str | None
    connection_id: int | None
    kind: str
    remote_name: str | None
    size: int | None
    remote_mtime: datetime | None
    sha256: str
    has_content: bool
    processing_status: str | None
    file_status: str | None
    ack_status: str | None
    sent_at: datetime | None = None
    ack_detail: str | None = None
    replaced_by_id: int | None = None
    isa13: str | None
    gs06: str | None
    st02: str | None
    partner_code: str | None
    error: str | None
    created_at: datetime
    created_by_name: str
    shipments_without_order: int = 0  # FR-OB-25c: its 945 sets that have no order
    # The orders and transfers it belongs to (user 2026-11-09): the first MAX_REFS, and how many in all;
    # with none, why (a Zoho 997, an order before the store's start, a duplicate …).
    orders: list[EntityRef] = []
    orders_total: int = 0
    transfers: list[EntityRef] = []
    no_order_reason: str | None = None


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    trigger: str
    status: str
    started_at: datetime
    heartbeat_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any] | None
    error: str | None
    scope: str | None = None  # "connection:<id>" | "system" (none: the one cycle, before per-connection scheduling)


class LogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    run_id: int | None
    level: str
    step: str
    connection_id: int | None
    document_id: int | None
    order_id: int | None
    transfer_id: int | None
    message: str
    context: dict[str, Any] | None


class RunStarted(BaseModel):
    run_id: int  # the global Run now: the system job's run (each connection runs first, in its own run)
