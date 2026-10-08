"""The PR PDF's Service Owner row — Service/Project types only.

The owner is who `app/tasks/service_gr_due.py` will chase months after this
document is filed, so the signed PDF has to say who it is. The row travels
with the expected completion date because together they are the claim the
document makes: this should be finished by then, and that person says it was.

The name is resolved by the caller (api/v1/pr.py::_owner_display_name, which
goes through crud.pr_owner) rather than in the renderer, exactly like the
budget account name — see test_pr_pdf_budget_account.py.
"""
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.department import Department
from app.models.pr import PrLineItem, PurchaseRequest
from app.schemas.auth import RegisterRequest
from app.services.pdf_pr import generate_pr_pdf
from tests.test_pr_pdf_department import _decompress_pdf_content_streams

pytestmark = pytest.mark.asyncio

_SHOWN_RE = re.compile(rb"\((.*?)\) Tj")

COMPLETION = date(2026, 11, 30)


def _shown_text(pdf_bytes: bytes) -> str:
    """Visible text, joined across ReportLab's per-line show-text operators."""
    text = _decompress_pdf_content_streams(pdf_bytes)
    joined = b" ".join(_SHOWN_RE.findall(text)).decode("latin-1")
    return re.sub(r"\s+", " ", joined)


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_pr(db, *, pr_type: int) -> PurchaseRequest:
    dept = Department(code=f"DO-{uuid.uuid4().hex[:6]}", name="Owner Row Dept")
    db.add(dept)
    await db.flush()
    user = await user_crud.create(db, RegisterRequest(
        email=f"owner-row-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Ryan Requester", role="requester", department_id=dept.id))
    await db.flush()
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Annual duct cleaning",
        type=pr_type, status="approved", amount=Decimal("1000.00"), currency="CAD",
        department_id=dept.id, department_name=dept.name, created_by=user.id,
        service_completion_date=COMPLETION,
        line_items=[PrLineItem(
            description="Cleaning", qty=Decimal("1.0000"), unit="ea",
            unit_price=Decimal("1000.00"), line_total=Decimal("1000.00"), sort_order=0,
        )],
    )
    db.add(pr)
    await db.flush()
    return pr


@pytest.mark.parametrize("pr_type", [4, 6])
async def test_service_and_project_prs_print_the_owner(test_engine, pr_type):
    async with _factory(test_engine)() as db:
        pr = await _seed_pr(db, pr_type=pr_type)
        shown = _shown_text(generate_pr_pdf(
            pr, "Test Co", None, None, "Ryan Requester", None, None, "Olive Owner",
        ))
    assert "Service Owner" in shown
    assert "Olive Owner" in shown
    # The pair travels together — a completion date with nobody accountable for
    # it is the gap this whole feature closes.
    assert "Expected Completion" in shown
    assert "2026-11-30" in shown


@pytest.mark.parametrize("pr_type", [1, 2, 3, 5])
async def test_physical_prs_have_no_owner_row(test_engine, pr_type):
    """Physical types never collect an owner, so the row would be noise. The
    admission case above is what makes this assertion mean something: without
    it, a row that never rendered at all would also pass here."""
    async with _factory(test_engine)() as db:
        pr = await _seed_pr(db, pr_type=pr_type)
        shown = _shown_text(generate_pr_pdf(
            pr, "Test Co", None, None, "Ryan Requester", None, None, "Olive Owner",
        ))
    assert "Service Owner" not in shown
    assert "Olive Owner" not in shown
    # Positive control on the same document: the meta grid did render.
    assert "Requested By" in shown and "Ryan Requester" in shown


async def test_the_row_renders_with_no_owner_name(test_engine):
    """A document must never fail to render over a missing name — same rule the
    budget account cell follows."""
    async with _factory(test_engine)() as db:
        pr = await _seed_pr(db, pr_type=4)
        shown = _shown_text(generate_pr_pdf(
            pr, "Test Co", None, None, "Ryan Requester", None, None, None,
        ))
    assert "Service Owner" in shown


async def test_an_ampersand_in_the_owner_name_survives(test_engine):
    """Paragraph parses its content as mini-XML, so an unescaped '&' swallows
    everything after it — the bug that ate budget account names."""
    async with _factory(test_engine)() as db:
        pr = await _seed_pr(db, pr_type=4)
        shown = _shown_text(generate_pr_pdf(
            pr, "Test Co", None, None, "Ryan Requester", None, None, "Ops & Facilities",
        ))
    assert "Ops & Facilities" in shown


async def test_owner_display_name_falls_back_to_the_requester(test_engine):
    """The renderer is handed a name, not a PR — this is the caller-side half,
    and it must resolve NULL owner_id to the requester rather than a dash."""
    from app.api.v1.pr import _owner_display_name

    async with _factory(test_engine)() as db:
        pr = await _seed_pr(db, pr_type=4)
        assert pr.owner_id is None
        assert await _owner_display_name(db, pr, "Ryan Requester") == "Ryan Requester"

        owner = await user_crud.create(db, RegisterRequest(
            email=f"olive-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Olive Owner", role="requester"))
        await db.flush()
        pr.owner_id = owner.id
        await db.flush()
        assert await _owner_display_name(db, pr, "Ryan Requester") == "Olive Owner"
