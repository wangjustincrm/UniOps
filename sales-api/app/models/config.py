"""Configuration tables (design §3.1; migration sales01_config).

Editable in the UI with CSV import / export (FR-OB-09). The six tables that
orders reference are Audited + Versioned + Deactivatable and are never deleted
(§13.1); partner codes, settings and notification rules are Audited (+ Versioned
where the UI edits rows by id). No seed data: configuration arrives by CSV.

Closed value sets (app/domain/config.py) are CHECK constraints, so a later
value is a migration, not an enum type change.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

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


class Connection(Audited, Versioned, Deactivatable, Base):
    """`secret_name` names the server secret (§5.3); the secret itself is
    never stored."""
    __tablename__ = "sales_connections"
    __table_args__ = (
        one_of("kind", CONNECTION_KINDS, "ck_sales_connections_kind"),
        one_of("after_read", AFTER_READ, "ck_sales_connections_after_read"),
        one_of("mode", CONNECTION_MODES, "ck_sales_connections_mode"),
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
