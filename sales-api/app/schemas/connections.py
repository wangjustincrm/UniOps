"""Connection DTOs (design §3.1 `sales_connections`, §5.1, §5.3). The secret
itself never appears: `secret_name` names an environment variable, and Read
says only whether it is present."""
from __future__ import annotations

from datetime import datetime, time
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import host_key_line, not_excel_number, secret_name

Kind = Literal["SFTP", "SHOPIFY"]
AfterRead = Literal["LEAVE", "MOVE", "DELETE"]
Mode = Literal["LIVE", "TEST"]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Host = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
User = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255), AfterValidator(not_excel_number)]
Folder = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Fixed messages, never the value (app/schemas/common.py).
SecretName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100), AfterValidator(secret_name)]
HostKeyLine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(host_key_line)]
Port = Annotated[int, Field(ge=1, le=65535)]
ApiVersion = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}-\d{2}$")]
PageSize = Annotated[int, Field(ge=1, le=250)]
Cutoff = Annotated[int, Field(ge=0)]
PartnerCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Seconds = Annotated[int, Field(ge=0)]
Minutes = Annotated[int, Field(ge=1, le=1440)]


def _days(v: str | None) -> str | None:
    """'mon, fri' → 'Mon,Fri' (week order); empty → None (every day)."""
    from app.domain.schedule import format_days, parse_days
    return format_days(parse_days(v)) if v is not None else None


Days = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40), AfterValidator(_days)]


def _window(m):
    """Both ends of the active window, or neither."""
    if (getattr(m, "active_from", None) is None) != (getattr(m, "active_to", None) is None):
        raise ValueError("give both the start and the end of the active hours, or neither")
    return m


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    kind: Kind
    host: Host | None = None
    port: Port | None = None
    username: User | None = None
    inbound_folder: Folder | None = None
    outbound_folder: Folder | None = None
    processed_folder: Folder | None = None
    error_folder: Folder | None = None
    test_inbound_folder: Folder | None = None
    test_outbound_folder: Folder | None = None
    after_read: AfterRead
    stable_seconds: Seconds = 60
    mode: Mode
    secret_name: SecretName | None = None
    host_key: HostKeyLine | None = None
    api_version: ApiVersion | None = None
    page_size: PageSize | None = None
    order_number_cutoff: Cutoff | None = None
    partner_code: PartnerCode | None = None
    # Its schedule (user 2026-11-02); minutes default by kind: SFTP 5, Shopify 10.
    schedule_minutes: Minutes | None = None
    schedule_paused: bool = False
    active_from: time | None = None
    active_to: time | None = None
    active_days: Days | None = None
    active: bool = True

    _check_window = model_validator(mode="after")(_window)


class ConnectionUpdate(BaseModel):
    """Required columns refuse null (see PartnerUpdate). `name` is the CSV key
    and stays. The login latch is cleared by its own action, not here."""
    model_config = ConfigDict(extra="forbid")

    kind: Kind = None  # type: ignore[assignment]
    host: Host | None = None
    port: Port | None = None
    username: User | None = None
    inbound_folder: Folder | None = None
    outbound_folder: Folder | None = None
    processed_folder: Folder | None = None
    error_folder: Folder | None = None
    test_inbound_folder: Folder | None = None
    test_outbound_folder: Folder | None = None
    after_read: AfterRead = None  # type: ignore[assignment]
    stable_seconds: Seconds = None  # type: ignore[assignment]
    mode: Mode = None  # type: ignore[assignment]
    secret_name: SecretName | None = None
    host_key: HostKeyLine | None = None
    api_version: ApiVersion | None = None
    page_size: PageSize | None = None
    order_number_cutoff: Cutoff | None = None
    partner_code: PartnerCode | None = None
    schedule_minutes: Minutes = None  # type: ignore[assignment]
    schedule_paused: bool = None  # type: ignore[assignment]
    active_from: time | None = None
    active_to: time | None = None
    active_days: Days | None = None
    active: bool = None  # type: ignore[assignment]


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: Kind
    host: str | None
    port: int | None
    username: str | None
    inbound_folder: str | None
    outbound_folder: str | None
    processed_folder: str | None
    error_folder: str | None
    test_inbound_folder: str | None
    test_outbound_folder: str | None
    after_read: AfterRead
    stable_seconds: int
    mode: Mode
    secret_name: str | None
    secret_present: bool = False  # filled by the service; the value is never read out
    host_key: str | None
    auth_failed_at: datetime | None
    auth_failed_reason: str | None
    api_version: str | None
    cursor_updated_at: datetime | None  # system-written; reset through its own action
    page_size: int | None
    order_number_cutoff: int | None
    partner_code: str | None
    schedule_minutes: int
    schedule_paused: bool
    active_from: time | None
    active_to: time | None
    active_days: str | None
    # Written by the scheduler.
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_summary: str | None
    last_error: str | None
    next_run_at: datetime | None
    active: bool
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None


class HostKeyRead(BaseModel):
    line: str
    fingerprint: str
    pinned: bool    # the connection has a pin
    matches: bool   # the server's key is the pinned one


class FolderCheckRead(BaseModel):
    folder: str
    ok: bool
    files: int | None = None
    error: str | None = None


class CursorReset(BaseModel):
    """The inclusive updated_at to pull from next (FR-OB-10); null = pull nothing."""
    cursor_updated_at: datetime | None


class ConnectionTestRead(BaseModel):
    """FR-OB-74: what the test saw; it creates, moves and deletes nothing.
    `folders` is empty unless the server's key matches the pin (no login to
    an unpinned or changed host)."""
    host_key: HostKeyRead | None
    folders: list[FolderCheckRead]
    error: str | None = None
    shop: dict | None = None  # Shopify: the store's name, domain and currency


class EdiSettings(BaseModel):
    """P4: the warehouse SFTP connection 940s are put to and 997s read from
    (none: no 940 is generated), and the 997 wait before Timeout (FR-OB-23)."""
    model_config = ConfigDict(extra="forbid")

    connection_id: int | None
    ack_timeout_minutes: Annotated[int, Field(ge=1, le=10080)]
    # P6: where 810 JSON invoices are put (TrueCommerce Import); none: an 810 is not sent.
    invoice_connection_id: int | None = None


class ShadowSetting(BaseModel):
    """The shadow run (§5.4): Zoho is live; Sales reads and sends nothing."""
    model_config = ConfigDict(extra="forbid")

    shadow_mode: bool
    # In the answer to switching on: the open orders marked as Zoho's.
    marked_orders: int | None = None


class SchedulerSettings(BaseModel):
    """The master switch for every job and the system job's interval (Health → Scheduler)."""
    model_config = ConfigDict(extra="forbid")

    paused: bool
    system_minutes: Minutes
