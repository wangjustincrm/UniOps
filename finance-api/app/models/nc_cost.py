"""NC65 inventory-accounting + cost mirror (AI Financial Analysis).

Mirrors the physical tables created by 0040_nc_cost_mirror column for column —
keep them that way; a mirror model that drifts from its table is a 500 waiting
for production. See that migration for why the scope is replaced wholesale on
every sync and why `dr` is kept.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"

_Q = Numeric(28, 8)
_M = Numeric(18, 2)


def _uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class _Mirror:
    dr: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    nc_ts: Mapped[str | None] = mapped_column(String(19), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NcIaLedger(_Mirror, Base):
    __tablename__ = "nc_ia_ledger"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    bill_type: Mapped[str] = mapped_column(String(4), nullable=False)
    bill_code: Mapped[str | None] = mapped_column(String(40))
    bill_id: Mapped[str | None] = mapped_column(String(25))
    bill_bid: Mapped[str | None] = mapped_column(String(25))
    row_no: Mapped[str | None] = mapped_column(String(20))
    biz_date: Mapped[date | None] = mapped_column(Date)
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    material_pk: Mapped[str] = mapped_column(String(25), nullable=False)
    material_code: Mapped[str | None] = mapped_column(String(40))
    material_name: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal | None] = mapped_column(_Q)
    price: Mapped[Decimal | None] = mapped_column(_Q)
    amount: Mapped[Decimal | None] = mapped_column(_M)
    batch_code: Mapped[str | None] = mapped_column(String(60))
    mo_code: Mapped[str | None] = mapped_column(String(40))
    src_type: Mapped[str | None] = mapped_column(String(20))
    src_code: Mapped[str | None] = mapped_column(String(60))
    first_code: Mapped[str | None] = mapped_column(String(60))
    upstream_type: Mapped[str | None] = mapped_column(String(20))
    upstream_code: Mapped[str | None] = mapped_column(String(60))
    upstream_bid: Mapped[str | None] = mapped_column(String(25))
    trantype_code: Mapped[str | None] = mapped_column(String(40))
    trantype_name: Mapped[str | None] = mapped_column(String(200))
    estimate_flag: Mapped[str | None] = mapped_column(String(1))
    audit_seq: Mapped[int | None] = mapped_column(Integer)
    price_mode: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(String(500))


class NcIaMonthBalance(_Mirror, Base):
    __tablename__ = "nc_ia_month_balances"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    material_pk: Mapped[str] = mapped_column(String(25), nullable=False)
    material_code: Mapped[str | None] = mapped_column(String(40))
    qty: Mapped[Decimal | None] = mapped_column(_Q)
    amount: Mapped[Decimal | None] = mapped_column(_M)
    price: Mapped[Decimal | None] = mapped_column(_Q)


class NcCmCostObject(_Mirror, Base):
    __tablename__ = "nc_cm_cost_objects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    code: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str | None] = mapped_column(String(300))
    material_pk: Mapped[str | None] = mapped_column(String(25))
    material_code: Mapped[str | None] = mapped_column(String(40))


class NcCmProdCost(_Mirror, Base):
    __tablename__ = "nc_cm_prod_costs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    cost_object_pk: Mapped[str | None] = mapped_column(String(25))
    material_pk: Mapped[str | None] = mapped_column(String(25))
    material_code: Mapped[str | None] = mapped_column(String(40))
    material_name: Mapped[str | None] = mapped_column(String(300))
    cost_center_pk: Mapped[str | None] = mapped_column(String(25))
    fee_type: Mapped[int | None] = mapped_column(SmallInteger)
    qty: Mapped[Decimal | None] = mapped_column(_Q)
    amount: Mapped[Decimal | None] = mapped_column(_M)


class NcCmProdCostLine(_Mirror, Base):
    __tablename__ = "nc_cm_prod_cost_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    prod_cost_pk: Mapped[str] = mapped_column(String(25), nullable=False)
    period: Mapped[str | None] = mapped_column(String(7))
    element_pk: Mapped[str | None] = mapped_column(String(25))
    element_code: Mapped[str | None] = mapped_column(String(40))
    element_name: Mapped[str | None] = mapped_column(String(200))
    material_pk: Mapped[str | None] = mapped_column(String(25))
    material_code: Mapped[str | None] = mapped_column(String(40))
    material_name: Mapped[str | None] = mapped_column(String(300))
    child_type: Mapped[int | None] = mapped_column(SmallInteger)
    qty: Mapped[Decimal | None] = mapped_column(_Q)
    price: Mapped[Decimal | None] = mapped_column(_Q)
    amount: Mapped[Decimal | None] = mapped_column(_M)


class NcCmStuffLine(_Mirror, Base):
    __tablename__ = "nc_cm_stuff_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    stuff_pk: Mapped[str] = mapped_column(String(25), nullable=False)
    bill_code: Mapped[str | None] = mapped_column(String(40))
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    cost_object_pk: Mapped[str | None] = mapped_column(String(25))
    cost_object_code: Mapped[str | None] = mapped_column(String(60))
    cost_object_name: Mapped[str | None] = mapped_column(String(300))
    source_type: Mapped[int | None] = mapped_column(SmallInteger)
    business_date: Mapped[date | None] = mapped_column(Date)
    element_code: Mapped[str | None] = mapped_column(String(40))
    element_name: Mapped[str | None] = mapped_column(String(200))
    material_pk: Mapped[str | None] = mapped_column(String(25))
    material_code: Mapped[str | None] = mapped_column(String(40))
    material_name: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal | None] = mapped_column(_Q)
    price: Mapped[Decimal | None] = mapped_column(_Q)
    amount: Mapped[Decimal | None] = mapped_column(_M)


class NcSoInvoiceLine(_Mirror, Base):
    __tablename__ = "nc_so_invoice_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nc_pk: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    invoice_pk: Mapped[str] = mapped_column(String(25), nullable=False)
    invoice_code: Mapped[str | None] = mapped_column(String(40))
    bill_date: Mapped[date | None] = mapped_column(Date)
    status_flag: Mapped[int | None] = mapped_column(SmallInteger)
    customer_code: Mapped[str | None] = mapped_column(String(60))
    customer_name: Mapped[str | None] = mapped_column(String(300))
    orig_currency: Mapped[str | None] = mapped_column(String(10))
    exchange_rate: Mapped[Decimal | None] = mapped_column(_Q)
    row_no: Mapped[str | None] = mapped_column(String(20))
    material_pk: Mapped[str | None] = mapped_column(String(25))
    material_code: Mapped[str | None] = mapped_column(String(40))
    material_name: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal | None] = mapped_column(_Q)
    amount: Mapped[Decimal | None] = mapped_column(_M)
    orig_amount: Mapped[Decimal | None] = mapped_column(_M)
    tax: Mapped[Decimal | None] = mapped_column(_M)


class NcCostSyncRun(Base):
    __tablename__ = "nc_cost_sync_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict | None] = mapped_column(JSONB)
    tie_out_ok: Mapped[bool | None] = mapped_column(Boolean)
    tie_out: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class FaConfig(Base):
    __tablename__ = "fa_config"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    thresholds: Mapped[dict | None] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=text("now()"))
