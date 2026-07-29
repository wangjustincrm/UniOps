"""PDF-level test for PR department display (Task 6).

Verifies that generate_pr_pdf() renders the PR's SELECTED department
(pr.department_name, derived from department_id per Task 2) rather than
the creator's own department — the cross-department case from the task
brief's Step 1 (create a PR under a department different from the logged-in
user's, generate PDF, confirm the Meta grid's Department field shows the
selected department).

Follows the seed pattern from tests/test_pr_crud_department.py /
tests/test_pr_department_backfill.py: build Department/User/PurchaseRequest/
PrLineItem ORM rows directly against the `test_engine` fixture via a local
async_sessionmaker, then call generate_pr_pdf() synchronously (the PR's
line_items relationship is already populated in the session — no lazy load
needed) and assert on the raw PDF bytes. Department names are kept ASCII so
a raw substring check on the reportlab-produced bytes is reliable (ASCII
text in a PDF's content stream is written literally, unlike names requiring
font subsetting/encoding).
"""
import base64
import re
import uuid
import zlib
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.department import Department
from app.models.pr import PrLineItem, PurchaseRequest
from app.schemas.auth import RegisterRequest
from app.services.pdf_pr import generate_pr_pdf

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _decompress_pdf_content_streams(pdf_bytes: bytes) -> bytes:
    """Best-effort decode of a reportlab-produced PDF's page content streams.

    reportlab encodes text streams as ASCII85 + Flate by default (see the
    `/Filter [ /ASCII85Decode /FlateDecode ]` seen on the page's Contents
    stream). There's no PDF text-extraction library in this codebase's
    dependencies, so this decodes just enough (ASCII85 -> zlib inflate) to
    let the test assert on the literal department-name substrings reportlab
    writes into Tj/TJ text-showing operators.
    """
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw
            if data.rstrip().endswith(b"~>"):
                data = data.rstrip()[:-2]
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue  # not every stream is ASCII85+Flate text (e.g. binary/font streams)
    return bytes(out)


@dataclass
class _Ctx:
    pr: PurchaseRequest
    creator_dept_name: str
    selected_dept_name: str


async def _seed_cross_department_pr(db: AsyncSession) -> _Ctx:
    """Creator belongs to Dept A; the PR they file is filed under Dept B
    (selected via the department selector, per Task 4/5) — proves the PDF
    must render Dept B, not the creator's own Dept A."""
    creator_dept = Department(code=f"DA-{uuid.uuid4().hex[:6]}", name="Creator Home Dept")
    selected_dept = Department(code=f"DB-{uuid.uuid4().hex[:6]}", name="Selected Filing Dept")
    db.add_all([creator_dept, selected_dept])
    await db.flush()

    user = await user_crud.create(db, RegisterRequest(
        email=f"pr-pdf-dept-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="PDF Dept Requester", role="requester", department_id=creator_dept.id))
    await db.flush()

    # Attach the line item via the relationship kwarg at construction time
    # (while the PR is still transient) so the in-memory collection is
    # already fully populated — appending to `.line_items` on an already-
    # flushed object would instead trigger a lazy DB load, which fails
    # outside an async/greenlet context.
    line_item = PrLineItem(
        description="Test widget", qty=Decimal("3"), unit="ea",
        unit_price=Decimal("50.00"), line_total=Decimal("150.00"), sort_order=0,
    )
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Cross-dept PDF PR", type=2,
        status="submitted", amount=Decimal("150.00"), currency="CAD",
        department_id=selected_dept.id, department_name=selected_dept.name,
        created_by=user.id, line_items=[line_item],
    )
    db.add(pr)
    await db.flush()

    return _Ctx(pr=pr, creator_dept_name=creator_dept.name, selected_dept_name=selected_dept.name)


@pytest.mark.asyncio
async def test_pdf_shows_selected_department_not_creators_own(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        ctx = await _seed_cross_department_pr(db)
        await db.commit()

        pdf_bytes = generate_pr_pdf(ctx.pr, company_name="Test Co", pdf_templates=None, logo_data_url=None)

        assert pdf_bytes[:4] == b"%PDF"  # sanity: a real PDF was produced

        text = _decompress_pdf_content_streams(pdf_bytes)
        assert ctx.selected_dept_name.encode("ascii") in text
        assert ctx.creator_dept_name.encode("ascii") not in text
