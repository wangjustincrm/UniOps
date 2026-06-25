"""GET /finance/v1/posting/events — query API tests."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.posting import PostingEvent, PostingLine


def _token(role: str = "finance_manager") -> str:
    payload = {
        "sub": str(uuid.uuid4()), "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_list_events_by_doc(client, db_session):
    doc_id = uuid.uuid4()
    ev = PostingEvent(
        source_service="epms", source_doc_type="pa", source_doc_id=doc_id,
        source_doc_number="PA-2026-0042", event_type="payment",
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(ev)
    await db_session.flush()
    db_session.add(PostingLine(event_id=ev.id, line_no=1, line_role="bank",
                               credit=Decimal("55.00")))
    await db_session.flush()

    r = await client.get(
        "/finance/v1/posting/events",
        params={"source_doc_type": "pa", "source_doc_id": str(doc_id)},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["source_doc_number"] == "PA-2026-0042"
    assert len(body[0]["lines"]) == 1
    assert body[0]["lines"][0]["credit"] == "55.00"


async def test_requires_auth(client):
    r = await client.get("/finance/v1/posting/events")
    assert r.status_code in (401, 403)
