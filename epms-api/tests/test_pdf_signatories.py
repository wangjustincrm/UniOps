"""Renders PR / PA / GR PDFs and asserts the people's names actually land on the page.

Reuses the content-stream decode from tests/test_pr_pdf_department.py: reportlab
writes page text as ASCII85 + Flate, and there is no PDF text-extraction library
in this project's dependencies. All seeded names are ASCII so a raw substring
check on the inflated stream is reliable (non-ASCII would need font subsetting).
"""
import base64
import re
import uuid
import zlib
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.signatories import approval_signatories
from app.models.approval import ApprovalEvent
from app.models.pa import PaLineItem, PaymentApplication
from app.models.pr import PrLineItem, PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services.pdf_pa import generate_pa_pdf
from app.services.pdf_pr import generate_pr_pdf

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _pdf_text(pdf_bytes: bytes) -> bytes:
    """Inflate a reportlab PDF's page content streams so text can be asserted on."""
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
        email=f"pdfsig-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name=full_name, role="requester"))
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_pr_pdf_prints_requester_and_approvers(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rex Requester")
        manager = await _user(db, "Mona Manager")
        gm = await _user(db, "Gus General")
        pr = PurchaseRequest(
            number=f"PR-{uuid.uuid4().hex[:8]}", title="PDF signatory PR", type=2,
            status="approved", amount=Decimal("150.00"), currency="CAD",
            created_by=requester.id,
            line_items=[PrLineItem(
                description="Widget", qty=Decimal("3"), unit="ea",
                unit_price=Decimal("50.00"), line_total=Decimal("150.00"), sort_order=0)],
        )
        db.add(pr)
        await db.flush()
        db.add_all([
            ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                          step_idx=0, action="approve", actor_id=manager.id,
                          actor_role="dept_manager"),
            ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                          step_idx=1, action="approve", actor_id=requester.id,
                          actor_role="director",
                          comment="Auto-skipped (department has no Director)"),
            ApprovalEvent(document_type="pr", document_id=pr.id, document_number=pr.number,
                          step_idx=2, action="approve", actor_id=gm.id,
                          actor_role="gm_or_opm"),
        ])
        await db.commit()

        requester_name, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)
        pdf_bytes = generate_pr_pdf(pr, "Test Co", None, None, requester_name, approvals)

        assert pdf_bytes[:4] == b"%PDF"
        text = _pdf_text(pdf_bytes)
        assert b"Requested By" in text
        assert b"Rex Requester" in text
        assert b"Approvals" in text
        assert b"Mona Manager" in text
        assert b"Gus General" in text
        # the auto-skipped step contributes neither a row nor a role label
        assert b"Director" not in text


@pytest.mark.asyncio
async def test_pr_pdf_without_signatories_still_renders(test_engine):
    """Guards the two legacy in-repo callers that pass only (pr, company_name)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Solo Requester")
        pr = PurchaseRequest(
            number=f"PR-{uuid.uuid4().hex[:8]}", title="Bare PR", type=2,
            status="approved", amount=Decimal("50.00"), currency="CAD",
            created_by=requester.id,
            line_items=[PrLineItem(
                description="Widget", qty=Decimal("1"), unit="ea",
                unit_price=Decimal("50.00"), line_total=Decimal("50.00"), sort_order=0)],
        )
        db.add(pr)
        await db.commit()

        pdf_bytes = generate_pr_pdf(pr, "Test Co")

        assert pdf_bytes[:4] == b"%PDF"
        assert b"Approvals" not in _pdf_text(pdf_bytes)


@pytest.mark.asyncio
async def test_pa_pdf_prints_applicant_and_approvers(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        applicant = await _user(db, "Amy Applicant")
        finance = await _user(db, "Fred Finance")
        vendor = Vendor(
            code=f"V{uuid.uuid4().hex[:6]}", name="PA PDF Vendor",
            category="general", contact_name="Vendor Contact",
            contact_email="vendor@example.com",
        )
        db.add(vendor)
        await db.flush()
        pa = PaymentApplication(
            pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="PDF signatory PA",
            vendor_id=vendor.id, vendor_name=vendor.name, pa_type="regular",
            subtotal=Decimal("100.00"), tax_amount=Decimal("0"),
            payment_amount=Decimal("100.00"), currency="CAD", status="approved",
            created_by=applicant.id,
            line_items=[PaLineItem(
                description="Service", qty=Decimal("1"), unit="ea",
                unit_price=Decimal("100.00"), line_total=Decimal("100.00"), sort_order=0)],
        )
        db.add(pa)
        await db.flush()
        db.add(ApprovalEvent(
            document_type="pa", document_id=pa.id, document_number=pa.pa_number,
            step_idx=0, action="approve", actor_id=finance.id,
            actor_role="finance_manager"))
        await db.commit()

        requester_name, approvals = await approval_signatories(db, "pa", pa.id, pa.created_by)
        pdf_bytes = generate_pa_pdf(pa, "Test Co", None, None, requester_name, approvals)

        assert pdf_bytes[:4] == b"%PDF"
        text = _pdf_text(pdf_bytes)
        assert b"Applied By" in text
        assert b"Amy Applicant" in text
        assert b"Approvals" in text
        assert b"Fred Finance" in text
        assert b"Finance Manager" in text
