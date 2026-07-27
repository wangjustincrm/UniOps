"""The single-document resync endpoint delegates to _resync_document."""
import uuid

import pytest


@pytest.mark.asyncio
async def test_resync_document_calls_engine(monkeypatch):
    from app.api.v1 import routing

    called = {}

    async def _fake_resync(db, doc_type, doc_id):
        called["args"] = (doc_type, str(doc_id))
        return {"doc_type": doc_type, "number": "PR-X", "actions": ["reissue"], "final_step": 1}

    monkeypatch.setattr(routing, "_resync_document", _fake_resync, raising=False)

    class _DB:
        async def commit(self): called["committed"] = True

    did = uuid.uuid4()
    result = await routing.resync_document(
        body={"doc_type": "pr", "doc_id": str(did)},
        db=_DB(), user={"role": "system_admin", "sub": str(uuid.uuid4())})
    assert called["args"] == ("pr", str(did))
    assert called["committed"] is True
    assert result["resynced"]["actions"] == ["reissue"]
