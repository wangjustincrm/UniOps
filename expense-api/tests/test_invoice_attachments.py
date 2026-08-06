"""invoice_source whitelist on the shared invoice attachment store."""
import uuid

import pytest


@pytest.mark.anyio
async def test_credit_source_passes_the_whitelist(admin_client):
    r = await admin_client.post(
        "/api/v1/invoice-attachments",
        params={"invoice_id": str(uuid.uuid4()), "invoice_source": "credit"},
        files={"file": ("cn.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    # 400 is the whitelist rejection specifically; any other status means the
    # guard let it through (storage may still fail without a live file-api).
    assert r.status_code != 400


@pytest.mark.anyio
async def test_unknown_source_is_still_rejected(admin_client):
    r = await admin_client.post(
        "/api/v1/invoice-attachments",
        params={"invoice_id": str(uuid.uuid4()), "invoice_source": "nonsense"},
        files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
    assert "invoice_source" in r.json()["detail"]
