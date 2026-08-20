"""GR PDF coverage: confirm generates a PDF too (not just acknowledge).

Root cause (docs/superpowers/plans/2026-08-10-gr-pdf-coverage.md): the confirm
branch of app.crud.gr.action() lets a service GR jump straight from
pending_ack to confirmed ("collapses acknowledge + confirm into one step"),
skipping the acknowledge branch — the ONLY call site of _attach_gr_pdf before
this fix. A production snapshot showed 20/20 confirmed service GRs and 719/771
confirmed physical GRs with no PDF attachment at all.

Follows the seeding style of tests/test_gr_three_way_handoff.py (ORM rows
against app.db.session.AsyncSessionLocal / a local async_sessionmaker on
test_engine) and tests/test_signatories.py / tests/test_pdf_signatories.py
(gr_signatories + generate_gr_pdf content assertions).
"""
import base64
import re
import uuid
import zlib
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.gr import action as gr_action
from app.crud.signatories import gr_signatories
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.gr_attachment import GrAttachment
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.schemas.gr import GrActionRequest
from app.services.pdf_gr import generate_gr_pdf

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _pdf_text(pdf_bytes: bytes) -> bytes:
    """Inflate a reportlab PDF's page content streams so text can be asserted on.

    Copied from tests/test_pdf_signatories.py's helper rather than imported
    across test modules.
    """
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw.rstrip()
            if data.endswith(b"~>"):
                data = data[:-2]
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue  # binary/font streams are not ASCII85+Flate text
    return bytes(out)


async def _user(db: AsyncSession, full_name: str):
    u = await user_crud.create(db, RegisterRequest(
        email=f"grpdf-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name=full_name, role="requester"))
    await db.flush()
    return u


async def _vendor(db: AsyncSession) -> Vendor:
    v = Vendor(
        code=f"V{uuid.uuid4().hex[:6]}", name="GR PDF Coverage Vendor",
        category="general", contact_name="Vendor Contact",
        contact_email="vendor@example.com",
    )
    db.add(v)
    await db.flush()
    return v


async def _po(db: AsyncSession, vendor: Vendor, created_by: uuid.UUID, gr_type_int: int, **extra) -> PurchaseOrder:
    po = PurchaseOrder(
        number=f"PO-{uuid.uuid4().hex[:8]}", title="GR PDF Coverage PO", type=gr_type_int,
        status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
        currency="CAD", subtotal=Decimal("0"), total=Decimal("0"),
        created_by=created_by,
        **extra,
    )
    db.add(po)
    await db.flush()
    return po


async def _pr(db: AsyncSession, created_by: uuid.UUID) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="GR PDF Coverage PR", type=2, created_by=created_by,
    )
    db.add(pr)
    await db.flush()
    return pr


def _gr(*, gr_type: str, procurement_type: int, po: PurchaseOrder, vendor: Vendor,
        created_by: uuid.UUID, status: str, **extra) -> GoodsReceipt:
    return GoodsReceipt(
        number=f"GR-{uuid.uuid4().hex[:8]}", title="GR PDF Coverage",
        po_id=po.id, po_number=po.number, vendor_id=vendor.id, vendor_name=vendor.name,
        gr_type=gr_type, procurement_type=procurement_type, currency="CAD",
        status=status, created_by=created_by,
        line_items=[GrLineItem(
            description="Widget", qty_ordered=Decimal("3"), qty_received=Decimal("3"),
            unit="ea", unit_price=Decimal("50.00"), line_total=Decimal("150.00"),
            condition="good", sort_order=0,
        )],
        **extra,
    )


async def _attachments(db: AsyncSession, gr_id: uuid.UUID, filename: str) -> list[GrAttachment]:
    return list((await db.execute(
        select(GrAttachment).where(GrAttachment.gr_id == gr_id, GrAttachment.filename == filename)
    )).scalars().all())


@pytest.mark.asyncio
async def test_service_gr_confirm_from_pending_ack_creates_exactly_one_pdf(test_engine):
    """This is the reported bug: it must fail before the confirm-branch fix."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Sam Service Requester")
        vendor = await _vendor(db)
        po = await _po(db, vendor, requester.id, gr_type_int=1)
        gr = _gr(
            gr_type="service", procurement_type=1, po=po, vendor=vendor,
            created_by=requester.id, status="pending_ack",
        )
        db.add(gr)
        await db.commit()
        await db.refresh(gr)

        req = GrActionRequest(action="confirm", collected_by="Sam Service Requester")
        result = await gr_action(db, gr, req, actor_id=requester.id, token=None)
        await db.commit()

        assert result.status == "confirmed"
        atts = await _attachments(db, gr.id, f"{gr.number}.pdf")
        assert len(atts) == 1
        assert atts[0].file_data is not None


@pytest.mark.asyncio
async def test_physical_gr_acknowledge_then_confirm_replaces_not_duplicates(test_engine):
    """Proves the replacement logic: acknowledge already attached a PDF; confirm
    must replace it, not add a second one."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Penny Physical Requester")
        vendor = await _vendor(db)
        pr = await _pr(db, requester.id)
        # A PR-linked GR is what routes acknowledge into "collection_pending"
        # (creating a collect task for the requester) rather than the no-PR
        # auto-complete shortcut in _auto_complete_requester_steps — needed so
        # acknowledge and confirm run as two genuinely separate steps here.
        po = await _po(db, vendor, requester.id, gr_type_int=2, pr_id=pr.id, pr_number=pr.number)
        gr = _gr(
            gr_type="physical", procurement_type=2, po=po, vendor=vendor,
            created_by=requester.id, status="pending_ack",
            pr_id=pr.id, pr_number=pr.number,
        )
        db.add(gr)
        await db.commit()
        await db.refresh(gr)

        ack_req = GrActionRequest(action="acknowledge", acknowledged_by="Penny Physical Requester")
        gr = await gr_action(db, gr, ack_req, actor_id=requester.id, token=None)
        await db.commit()
        await db.refresh(gr)

        atts_after_ack = await _attachments(db, gr.id, f"{gr.number}.pdf")
        assert len(atts_after_ack) == 1
        assert gr.status == "collection_pending"   # acknowledge lands physical GRs here

        # confirm()'s allowed-status set includes "collection_pending" directly
        # (as well as "collected") — no separate collect step required to reach
        # confirm from here.
        confirm_req = GrActionRequest(action="confirm", collected_by="Penny Physical Requester")
        gr = await gr_action(db, gr, confirm_req, actor_id=requester.id, token=None)
        await db.commit()

        assert gr.status == "confirmed"
        atts_after_confirm = await _attachments(db, gr.id, f"{gr.number}.pdf")
        assert len(atts_after_confirm) == 1, (
            f"expected exactly one {gr.number}.pdf attachment after "
            f"acknowledge->collect->confirm, got {len(atts_after_confirm)}"
        )


@pytest.mark.asyncio
async def test_gr_signatories_resolves_collected_by(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        creator = await _user(db, "Cara Creator")
        collector = await _user(db, "Colin Collector")
        vendor = await _vendor(db)
        po = await _po(db, vendor, creator.id, gr_type_int=1)
        gr = _gr(
            gr_type="service", procurement_type=1, po=po, vendor=vendor,
            created_by=creator.id, status="confirmed",
            collected_by=str(collector.id),   # non-browser caller stored a raw UUID
        )
        db.add(gr)
        await db.commit()

        sig = await gr_signatories(db, gr)

        assert sig["collected_by"] == "Colin Collector"


@pytest.mark.asyncio
async def test_gr_pdf_prints_collected_by(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        creator = await _user(db, "Chuck Creator")
        collector = await _user(db, "Colleen Collector")
        vendor = await _vendor(db)
        po = await _po(db, vendor, creator.id, gr_type_int=1)
        gr = _gr(
            gr_type="service", procurement_type=1, po=po, vendor=vendor,
            created_by=creator.id, status="confirmed",
            collected_by="Colleen Collector",
        )
        db.add(gr)
        await db.commit()

        sig = await gr_signatories(db, gr)
        pdf_bytes = generate_gr_pdf(
            gr, "Test Co", None, None,
            sig["created_by_name"], sig["received_by"], sig["acknowledged_by"],
            sig["collected_by"],
        )

        assert pdf_bytes[:4] == b"%PDF"
        text = _pdf_text(pdf_bytes)
        assert b"Collected By" in text
        assert b"Colleen Collector" in text
