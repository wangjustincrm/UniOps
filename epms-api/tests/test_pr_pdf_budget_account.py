"""The PR PDF's Budget Code cell shows code + account name.

The name lives in budget-api's catalog (epms-api deliberately keeps no budget
tables of its own), so generate_pr_pdf() takes it as an argument and the
callers resolve it. Two things are worth pinning down: the cell renders both
halves when the name is known, and it still renders the code when budget-api
can't be reached -- a document must never fail to render over a missing name.
"""
import re
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.department import Department
from app.models.pr import PrLineItem, PurchaseRequest
from app.schemas.auth import RegisterRequest
from app.services import budget_client
from app.services.pdf_pr import generate_pr_pdf
from tests.test_pr_pdf_department import _decompress_pdf_content_streams


_SHOWN_RE = re.compile(rb"\((.*?)\) Tj")


def _shown_text(pdf_bytes: bytes) -> str:
    """The document's visible text, joined across ReportLab's line breaks.

    A Paragraph wider than its cell is emitted as several `(...) Tj` operators,
    one per rendered line -- "CRM00901 - Building Repairs and" then
    "maintenance" -- so a raw substring search for the full account name misses.
    Joining the show-text literals with a space puts the cell back together.
    """
    text = _decompress_pdf_content_streams(pdf_bytes)
    joined = b" ".join(_SHOWN_RE.findall(text)).decode("latin-1")
    return re.sub(r"\s+", " ", joined)


async def _seed_pr(db: AsyncSession) -> PurchaseRequest:
    dept = Department(code=f"DB-{uuid.uuid4().hex[:6]}", name="Budget Cell Dept")
    db.add(dept)
    await db.flush()
    user = await user_crud.create(db, RegisterRequest(
        email=f"budget-cell-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Budget Cell Requester", role="requester", department_id=dept.id))
    await db.flush()
    line_item = PrLineItem(
        description="Widget", qty=Decimal("30.0000"), unit="ea",
        unit_price=Decimal("10.18"), line_total=Decimal("305.40"), sort_order=0,
    )
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Budget cell PR", type=2,
        status="submitted", amount=Decimal("305.40"), currency="CAD",
        budget_code="CRM00901", department_id=dept.id, department_name=dept.name,
        created_by=user.id, line_items=[line_item],
    )
    db.add(pr)
    await db.flush()
    return pr


@pytest.mark.asyncio
async def test_budget_cell_shows_code_and_name(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = await _seed_pr(db)
        await db.commit()

        shown = _shown_text(generate_pr_pdf(
            pr, company_name="Test Co", pdf_templates=None, logo_data_url=None,
            budget_account_name="Building Repairs and maintenance",
        ))
        assert "CRM00901" in shown
        assert "Building Repairs and maintenance" in shown


@pytest.mark.asyncio
async def test_budget_cell_falls_back_to_code_alone(test_engine):
    """No name resolved (budget-api down, or a legacy PR) -- the code still prints."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = await _seed_pr(db)
        await db.commit()

        shown = _shown_text(generate_pr_pdf(
            pr, company_name="Test Co", pdf_templates=None, logo_data_url=None,
        ))
        assert "CRM00901" in shown
        assert "Building Repairs" not in shown


@pytest.mark.asyncio
async def test_account_name_with_ampersand_is_escaped(test_engine):
    """16 of the 168 accounts have "&" in the name; the no-space form is the
    one Paragraph's mini-XML parser mangles into "Food &Beverage;"."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = await _seed_pr(db)
        await db.commit()

        shown = _shown_text(generate_pr_pdf(
            pr, company_name="Test Co", pdf_templates=None, logo_data_url=None,
            budget_account_name="Food&Beverage for hospitality",
        ))
        assert "Food" in shown and "Beverage for hospitality" in shown
        assert "&Beverage;" not in shown   # the unescaped rendering


def _patch_transport(monkeypatch, handler) -> None:
    """Route budget_client's own httpx.AsyncClient through a mock transport."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(budget_client.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_get_account_name_matches_by_code(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/v1/accounts")
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json=[
            {"code": "CRM00900", "name": "Wrong one"},
            {"code": "CRM00901", "name": "Building Repairs & maintenance"},
        ])

    _patch_transport(monkeypatch, handler)
    assert await budget_client.get_account_name("t", "CRM00901") == "Building Repairs & maintenance"
    assert await budget_client.get_account_name("t", "NO-SUCH-CODE") is None
    assert await budget_client.get_account_name("t", None) is None


@pytest.mark.asyncio
async def test_get_account_name_fails_open(monkeypatch):
    """budget-api unreachable or refusing the token -> None, never an exception."""
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("budget-api down", request=request)

    _patch_transport(monkeypatch, boom)
    assert await budget_client.get_account_name("t", "CRM00901") is None

    _patch_transport(monkeypatch, lambda request: httpx.Response(403, json={"detail": "Not authenticated"}))
    assert await budget_client.get_account_name(None, "CRM00901") is None
