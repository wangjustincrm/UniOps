"""Read-only views of tables **other services own**: EPMS's purchase orders and
mdm's material master.

MRP's Inventory feature answers "how much of this do we have, and how much is
on the way". The first half is mrp-api's own `wms_inventory_lots`; the second
half lives in EPMS. All UniOps services share one Postgres database, so these
are direct reads rather than HTTP calls: the alternative is an N+1 round trip
over ~90 rows against endpoints that carry per-user department scoping which
means nothing for a service-to-service aggregate.

Three rules make that safe.

**1. Their own declarative Base, not `app.db.base.Base`.** mrp-api's alembic
autogenerates against `Base.metadata`; putting another service's tables in
there would have MRP's migration chain proposing to CREATE — or worse, ALTER —
tables EPMS owns. `EpmsMirrorBase` is invisible to alembic by construction, not
by anyone remembering to exclude it. Joins across the two Bases are ordinary
SQLAlchemy: they are just Table objects, and every join condition here is
written explicitly.

**2. Never written.** No inserts, no updates, no migrations. If a value looks
wrong, it is wrong in EPMS or mdm and is fixed there.

**3. The column contract is tested in the OWNING service.** A guard test here
would be circular: mrp-api's test database is built from mrp-api's migrations,
which do not create these tables, so the test fixtures would create them from
the definitions below and always agree with themselves. Instead
`epms-api/tests/test_mrp_consumed_columns.py` and
`mdm-api/tests/test_mrp_consumed_columns.py` assert these columns still exist
in the schemas their owners build — so a rename fails in the suite of whoever
does the renaming, which is the only place the failure is actionable. Without
that, an EPMS rename turns the in-transit column silently into zero, a number
indistinguishable from "nothing is on order".

Only the columns MRP reads are mapped. A narrow mirror is a smaller contract.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EpmsMirrorBase(DeclarativeBase):
    """Metadata for tables owned elsewhere. Deliberately separate from
    `app.db.base.Base` — see this module's docstring."""


class EpmsPurchaseOrder(EpmsMirrorBase):
    """EPMS `purchase_orders`, owned by epms-api."""
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(40))
    # 1=Raw Materials (packaging lives here too), 2=Consumables, 3=Spare Parts,
    # 4=Service, 5=Fixed Assets, 6=Software. MRP only ever looks at 1.
    type: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    vendor_name: Mapped[str] = mapped_column(String(255))
    # Hand-entered on UniOps-native POs; empty on every NC-synced one, which is
    # why the line-level date below exists.
    expected_delivery: Mapped[date | None] = mapped_column(Date)


class EpmsPoLineItem(EpmsMirrorBase):
    """EPMS `po_line_items`, owned by epms-api."""
    __tablename__ = "po_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"))
    # NULL on 96% of all PO lines — services, consumables and spares are free
    # text. Every open type-1 line has one (161 of 161), which is why MRP can
    # rely on it for raw materials and packaging and nothing else.
    material_id: Mapped[str | None] = mapped_column(String(50))
    qty: Mapped[Decimal] = mapped_column(Numeric(15, 4))
    received_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4))
    unit: Mapped[str] = mapped_column(String(30))
    # The ERP's own planned arrival date for this line
    # (NCSC.PO_ORDER_B.DPLANARRVDATE via the NC purchase sync).
    planned_arrival_date: Mapped[date | None] = mapped_column(Date)


class MdmMaterial(EpmsMirrorBase):
    """mdm's `materials` master, owned by mdm-api.

    Mirrored for two things a stock screen cannot do without: the material's
    name and unit, and `erp_class_code` — the ERP/NC classification MRP uses to
    keep raw milk out of every stock and on-order figure.

    Read here rather than through mdm-api's HTTP client on purpose. The name
    lookup in `app/services/mdm_client.py` never raises: it degrades every name
    to None so a stock read never 5xx's because master data is slow. That is
    right for a NAME and wrong for an EXCLUSION — a class that degrades to None
    silently stops excluding, and raw milk reappears in the numbers with nothing
    on screen to say so.
    """
    __tablename__ = "materials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(String(255))
    base_uom: Mapped[str | None] = mapped_column(String(20))
    shelf_life_months: Mapped[int | None] = mapped_column(Integer)
    # ERP/NC 物料基本分类. '0101' = Raw Milk.
    erp_class_code: Mapped[str | None] = mapped_column(String(20))
    erp_class_name: Mapped[str | None] = mapped_column(String(100))
