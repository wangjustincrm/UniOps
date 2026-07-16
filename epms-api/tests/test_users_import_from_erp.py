"""Test the import-from-erp users endpoint with a stubbed mdm-api client."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.schemas.auth import RegisterRequest


def _mock_mdm_client(person: dict | None = None, supplier_calls_raise=False) -> MagicMock:
    """Build a MagicMock that mimics `MdmClient(bearer)` returning an async ctx mgr."""
    stub = AsyncMock()
    if supplier_calls_raise:
        stub.get_person.side_effect = Exception("boom")
    else:
        stub.get_person.return_value = person
    ctx = AsyncMock()
    ctx.__aenter__.return_value = stub
    ctx.__aexit__.return_value = None
    constructor = MagicMock(return_value=ctx)
    return constructor


@pytest.mark.asyncio
async def test_import_from_erp_creates_user(admin_client):
    person = {
        "erp_person_code": "100100",
        "person_name": "Ayan Asim",
        "department_code": None,
        "department_name": "Production",
        "company_code": "10024",
        "company_name": "Canada Royal Milk ULC",
        "is_valid": True,
    }
    with patch("app.api.v1.users.MdmClient", _mock_mdm_client(person=person)):
        resp = await admin_client.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "100100", "email": "ayan@royalmilk.com"}]},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["email"] == "ayan@royalmilk.com"
    assert len(data["created"][0]["temp_password"]) >= 12
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_import_from_erp_records_error_when_person_missing(admin_client):
    with patch("app.api.v1.users.MdmClient", _mock_mdm_client(person=None)):
        resp = await admin_client.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "999999", "email": "x@royalmilk.com"}]},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == []
    assert data["errors"][0]["erp_person_code"] == "999999"
    assert "not found" in data["errors"][0]["reason"].lower()


@pytest.mark.asyncio
async def test_import_from_erp_rejects_empty_email(admin_client):
    resp = await admin_client.post(
        "/api/v1/users/import-from-erp",
        json={"items": [{"erp_person_code": "100100", "email": ""}]},
    )
    assert resp.status_code == 422


# ── Singleton-post guard: this endpoint bypassed `_post_conflict` entirely ──
# (it writes `role=role,` straight into the `User(...)` constructor). Closing
# that gap here — see app/api/v1/users.py::import_users_from_erp.
#
# NOTE on test isolation: `test_engine` is session-scoped and shared with every
# other test module; several of them create real holders of these same
# singleton roles via fixtures without cleaning up (e.g. test_dashboard.py's
# `procurement_client`/`vendor_mgr_client`, conftest's `finance_client`).
# Tests below that need a deterministic "zero pre-existing holders" or
# "exactly one holder" precondition explicitly clear the role first.

async def _clear_role_holders(test_engine, role: str) -> None:
    """Strip `role` off anyone currently holding it (PRIMARY or ADDITIONAL)."""
    async with test_engine.begin() as conn:
        await conn.execute(sa.text("UPDATE users SET role = 'requester' WHERE role = :role"), {"role": role})
        await conn.execute(sa.text("DELETE FROM user_roles WHERE role_code = :role"), {"role": role})


async def _make_user(test_engine, role: str = "requester") -> str:
    """Create a user directly in the test DB; return its id (str)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:8]}@erp-import-test.com",
                password="TestPass1!",
                full_name=f"ERP Import Test {role.title()}",
                role=role,
            ),
        )
        await db.commit()
    return str(user.id)


def _mock_mdm_client_by_code(persons: dict[str, dict]) -> MagicMock:
    """Like `_mock_mdm_client` but keyed by erp_person_code, for multi-item requests."""
    stub = AsyncMock()

    async def _get_person(code: str):
        return persons.get(code)

    stub.get_person.side_effect = _get_person
    ctx = AsyncMock()
    ctx.__aenter__.return_value = stub
    ctx.__aexit__.return_value = None
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_import_from_erp_rejects_singleton_post_held_by_another_user(test_engine, admin_client):
    """A CSV/ERP row claiming `gm` while someone else already holds it must be
    a per-row error (naming the current holder), not a silent role overwrite."""
    await _clear_role_holders(test_engine, "gm")
    holder_id = await _make_user(test_engine, "gm")

    claimant_email = f"gm-claimant-{uuid.uuid4().hex[:8]}@royalmilk.com"
    person = {"erp_person_code": "200200", "person_name": "New GM Claimant", "department_code": None}
    with patch("app.api.v1.users.MdmClient", _mock_mdm_client_by_code({"200200": person})):
        resp = await admin_client.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "200200", "email": claimant_email, "role": "gm"}]},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == []
    assert len(data["errors"]) == 1
    assert data["errors"][0]["erp_person_code"] == "200200"
    assert "singleton post" in data["errors"][0]["reason"]
    assert holder_id in data["errors"][0]["reason"]


@pytest.mark.asyncio
async def test_import_from_erp_two_items_same_singleton_post_second_rejected(test_engine, admin_client):
    """Two rows in the SAME request both claiming `finance_manager` — the DB
    won't see the first row's write before the second is checked unless we
    also track claims made earlier in this same batch. Uses a role distinct
    from the one used in test_user_supervisor_assignment.py's idempotent-
    resave test (`opm`) so this test's successful first claim can never leak
    into that test's "must be the sole holder" precondition."""
    await _clear_role_holders(test_engine, "finance_manager")
    email_first = f"fm-first-{uuid.uuid4().hex[:8]}@royalmilk.com"
    email_second = f"fm-second-{uuid.uuid4().hex[:8]}@royalmilk.com"
    persons = {
        "300300": {"erp_person_code": "300300", "person_name": "First FM Claimant", "department_code": None},
        "300301": {"erp_person_code": "300301", "person_name": "Second FM Claimant", "department_code": None},
    }
    with patch("app.api.v1.users.MdmClient", _mock_mdm_client_by_code(persons)):
        resp = await admin_client.post(
            "/api/v1/users/import-from-erp",
            json={"items": [
                {"erp_person_code": "300300", "email": email_first, "role": "finance_manager"},
                {"erp_person_code": "300301", "email": email_second, "role": "finance_manager"},
            ]},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["email"] == email_first
    assert len(data["errors"]) == 1
    assert data["errors"][0]["erp_person_code"] == "300301"
    assert "singleton post" in data["errors"][0]["reason"]
    assert "300300" in data["errors"][0]["reason"]
