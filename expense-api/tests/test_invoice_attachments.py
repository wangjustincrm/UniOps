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


@pytest.mark.anyio
@pytest.mark.parametrize("filename,sent,expected", [
    # Chrome on a PC without Outlook registered sends no type for .msg.
    ("MSC re credit.msg", "application/octet-stream", "application/vnd.ms-outlook"),
    ("reply.EML", "application/octet-stream", "message/rfc822"),
    # What the browser says wins whenever it says something.
    ("scan.pdf", "application/pdf", "application/pdf"),
    ("odd.msg", "text/plain", "text/plain"),
    # Unknown extension stays octet-stream, for file-api to refuse.
    ("thing.bin", "application/octet-stream", "application/octet-stream"),
])
async def test_saved_emails_are_typed_from_their_extension(
        admin_client, monkeypatch, filename, sent, expected):
    from app.api.v1 import invoice_attachments as mod
    seen = {}

    async def _fake_upload(data, filename, content_type, **kw):
        seen["content_type"] = content_type
        return uuid.uuid4()

    monkeypatch.setattr(mod, "upload_to_file_server", _fake_upload)
    r = await admin_client.post(
        "/api/v1/invoice-attachments",
        params={"invoice_id": str(uuid.uuid4()), "invoice_source": "credit"},
        files={"file": (filename, b"x", sent)},
    )
    assert r.status_code == 201, r.text
    # Both what file-api is sent and what is recorded here.
    assert seen["content_type"] == expected
    assert r.json()["content_type"] == expected
