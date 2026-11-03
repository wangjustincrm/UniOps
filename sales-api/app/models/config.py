"""Configuration tables (design §3.1; migration sales01_config).

Editable in the UI with CSV import / export (FR-OB-09). The six tables that
orders reference are Audited + Versioned + Deactivatable and are never deleted
(§13.1); partner codes, settings and notification rules are Audited (+ Versioned
where the UI edits rows by id). No seed data: configuration arrives by CSV.

Closed value sets (app/domain/config.py) are CHECK constraints, so a later
value is a migration, not an enum type change.
"""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Time,
    UniqueConstraint, false, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.config import (
    AFTER_READ, CHANNELS, CONNECTION_KINDS, CONNECTION_MODES, INVOICE_METHODS, NOTIFICATION_CHANNELS,
    SENDING_MODES, SETTLEMENT_MODES, WAREHOUSE_ROLES,
)
from app.platform.entities import Audited, Deactivatable, Versioned
from app.platform.settings_registry import SettingRecordMixin


def one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class Partner(Audited, Versioned, Deactivatable, Base):
    __tablename__ = "sales_partners"
    __table_args__ = (
        Index("uq_sales_partners_abbrev", text("upper(abbrev)"), unique=True,
              postgresql_where=text("abbrev IS NOT NULL AND abbrev <> ''")),
        one_of("channel", CHANNELS, "ck_sales_partners_channel"),
        one_of("settlement_mode", SETTLEMENT_MODES, "ck_sales_partners_settlement_mode"),
        one_of("sending_mode", SENDING_MODES, "ck_sales_partners_sending_mode"),
        one_of("invoice_method", INVOICE_METHODS, "ck_sales_partners_invoice_method"),
        # Codes are unique ignoring case (sales02). The exact unique key on
        # `code` stays: the DC routing and partner-code FKs reference it.
        Index("uq_sales_partners_code_ci", text("lower(code)"), unique=True),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)  # immutable after first document
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    abbrev: Mapped[str | None] = mapped_column(String(50), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    settlement_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    sending_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_method: Mapped[str] = mapped_column(String(20), nullable=False)
    invoice_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_cc: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dc_routing_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    min_shelf_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    carrier_defaults: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Item(Audited, Versioned, Deactivatable, Base):
    __tablename__ = "sales_items"

    item_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    upc: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gross_weight_lb_per_ea: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    warehouse_held: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Edited as part of the item (P3); loaded with it.
    units: Mapped[list[ItemUnit]] = relationship(lazy="selectin", order_by="ItemUnit.id")


class ItemUnit(Audited, Versioned, Deactivatable, Base):
    """FR-OB-02: the EA factor is 1 by definition; every other unit needs its
    own row, never a default."""
    __tablename__ = "sales_item_units"
    __table_args__ = (
        UniqueConstraint("item_code", "unit", name="uq_sales_item_units_item_unit"),
        CheckConstraint("unit <> 'EA' OR factor_ea = 1", name="ck_sales_item_units_ea_is_one"),
    )

    item_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("sales_items.item_code"), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    factor_ea: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Comma list of Shopify variant titles that mean this unit (PRD A.11), on
    # top of the unit's own name — e.g. "2-Can, 2-Pack" for 2PACK.
    shopify_titles: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ItemPartnerCode(Audited, Versioned, Base):
    """FR-OB-02: buyer part numbers are scoped per partner."""
    __tablename__ = "sales_item_partner_codes"
    __table_args__ = (
        UniqueConstraint("partner_code", "buyer_part", name="uq_sales_item_partner_codes_partner_part"),
    )

    partner_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("sales_partners.code"), nullable=False)
    buyer_part: Mapped[str] = mapped_column(String(100), nullable=False)
    item_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("sales_items.item_code"), nullable=False)


class DcRouting(Audited, Versioned, Deactivatable, Base):
    __tablename__ = "sales_dc_routing"
    __table_args__ = (
        UniqueConstraint("partner_code", "ship_to_gln", name="uq_sales_dc_routing_partner_gln"),
    )

    partner_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("sales_partners.code"), nullable=False)
    ship_to_gln: Mapped[str] = mapped_column(String(50), nullable=False)
    dc_number: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Warehouse(Audited, Versioned, Deactivatable, Base):
    __tablename__ = "sales_warehouses"
    __table_args__ = (one_of("role", WAREHOUSE_ROLES, "ck_sales_warehouses_role"),)

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address_lines: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # sales12: the 943's party (FR-OB-27): N1 qualifier and id (94 / CRM), N3, N4.
    edi_id_qualifier: Mapped[str | None] = mapped_column(String(5), nullable=True)
    edi_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    province: Mapped[str | None] = mapped_column(String(10), nullable=True)
    zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(5), nullable=True)


class Connection(Audited, Versioned, Deactivatable, Base):
    """`secret_name` names the server secret (§5.3); the secret itself is
    never stored."""
    __tablename__ = "sales_connections"
    __table_args__ = (
        one_of("kind", CONNECTION_KINDS, "ck_sales_connections_kind"),
        one_of("after_read", AFTER_READ, "ck_sales_connections_after_read"),
        one_of("mode", CONNECTION_MODES, "ck_sales_connections_mode"),
        # The store's orders belong to one partner; an SFTP channel's partner
        # comes from each file's content (850 TradingPartner). sales04.
        CheckConstraint("kind = 'SHOPIFY' OR partner_code IS NULL", name="ck_sales_connections_partner_shopify_only"),
        CheckConstraint("page_size IS NULL OR page_size BETWEEN 1 AND 250", name="ck_sales_connections_page_size"),
        CheckConstraint("schedule_minutes >= 1", name="ck_sales_connections_schedule_minutes"),
        CheckConstraint("(active_from IS NULL) = (active_to IS NULL)", name="ck_sales_connections_active_window"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inbound_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    outbound_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_inbound_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_outbound_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_read: Mapped[str] = mapped_column(String(10), nullable=False)
    stable_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    auth_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auth_failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The server's public host key ("ssh-ed25519 AAAA…"), pinned by an admin (§5.1;
    # sales03). No pin, no connection: the gateway never trusts a first key.
    host_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Shopify, per store (option 1, product owner 2026-10-29; sales04): the
    # channel's settings live on its row, only secrets in the environment.
    api_version: Mapped[str | None] = mapped_column(String(20), nullable=True)      # was SHOPIFY_API_VERSION
    # The inclusive updated_at cursor (FR-OB-10; was SHOPIFY_LAST_SYNC_DATE):
    # written by the intake only; a person resets it through an audited action.
    # NULL = pull nothing.
    cursor_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    page_size: Mapped[int | None] = mapped_column(Integer, nullable=True)            # orders per page (≤ 250)
    order_number_cutoff: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FR-OB-17 (9000 today)
    partner_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("sales_partners.code", name="fk_sales_connections_partner_code"), nullable=True)
    # Its own schedule (sales14; user 2026-11-02): every N minutes, unless
    # paused, inside the optional window (Toronto time; an end before the start
    # crosses midnight; days "Mon,Tue,…", empty = every day).
    schedule_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    schedule_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    active_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    active_to: Mapped[time | None] = mapped_column(Time, nullable=True)
    active_days: Mapped[str | None] = mapped_column(String(27), nullable=True)
    # Written by the scheduler only.
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_run_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Audited, SettingRecordMixin, Base):
    """Values of the typed settings registry (§13.13): scheduler interval and
    pause, Shopify cut-off, ACK timeout, … — one row per key."""
    __tablename__ = "sales_settings"


class NotificationRule(Audited, Versioned, Deactivatable, Base):
    """FR-OB-56. `recipients`: user ids and external addresses."""
    __tablename__ = "sales_notification_rules"
    __table_args__ = (one_of("channel", NOTIFICATION_CHANNELS, "ck_sales_notification_rules_channel"),)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    recipients: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The last test notification (go-live gate, PRD section 11; sales13).
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    test_result: Mapped[str | None] = mapped_column(Text, nullable=True)


class Carrier(Audited, Versioned, Deactivatable, Base):
    """A carrier the warehouse names in its 945 (W27-02, its SCAC, e.g. PRLA)
    and how to follow a parcel: `tracking_url` with `{tracking}` (sales19)."""
    __tablename__ = "sales_carriers"

    scac: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
