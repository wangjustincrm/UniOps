"""协议附件 —— 上传 / 列出 / 下载 / 删除。"""
import uuid

import pytest
from fastapi.responses import Response

from tests.test_agreements import AGR_URL, _agr_payload, seed_vendor_and_user

pytestmark = pytest.mark.asyncio


class _FakeFileServer:
    """In-memory stand-in for the file-api sidecar.

    The real file-api validates the caller's bearer token against its OWN
    JWT_SECRET_KEY (a live container's dev secret), which does not match this
    suite's standard JWT_SECRET_KEY=test-secret — hitting the real container
    made these three tests fail under the documented test invocation and
    require a sidecar nobody else (including CI) has running. This fake keeps
    upload/download/delete deterministic and container-free, while still
    proving real behaviour: download round-trips the exact bytes that were
    uploaded, and delete records exactly which storage_key was asked for.
    """

    def __init__(self):
        self.store: dict[uuid.UUID, tuple[bytes, str]] = {}
        self.deleted: list[uuid.UUID] = []

    async def upload(self, data, filename, content_type, doc_type, doc_id, bearer_token):
        key = uuid.uuid4()
        self.store[key] = (data, content_type)
        return key

    async def download(self, storage_key, bearer_token):
        data, content_type = self.store[storage_key]
        return Response(content=data, media_type=content_type)

    async def delete(self, storage_key, bearer_token):
        self.deleted.append(storage_key)
        self.store.pop(storage_key, None)


@pytest.fixture(autouse=True)
def fake_file_server(monkeypatch):
    """Patch the three outbound helpers AS IMPORTED into the router module —
    `app.api.v1.agreement_attachments.upload_to_file_server` etc. — not at
    their definition site in `app.services.attachment_helper`, since the
    router already bound the original names at import time.
    """
    fake = _FakeFileServer()
    monkeypatch.setattr("app.api.v1.agreement_attachments.upload_to_file_server", fake.upload)
    monkeypatch.setattr("app.api.v1.agreement_attachments.proxy_download", fake.download)
    monkeypatch.setattr("app.api.v1.agreement_attachments.delete_from_file_server", fake.delete)
    return fake


@pytest.fixture
async def agreement_id(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))).json()
    return created["id"]


async def test_upload_then_list_returns_the_file(admin_client, agreement_id):
    res = await admin_client.post(
        f"{AGR_URL}/{agreement_id}/attachments",
        files={"file": ("contract.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 201, res.text
    assert res.json()["filename"] == "contract.pdf"

    listed = await admin_client.get(f"{AGR_URL}/{agreement_id}/attachments")
    assert [a["filename"] for a in listed.json()] == ["contract.pdf"]


async def test_delete_removes_it(admin_client, agreement_id, fake_file_server):
    up = await admin_client.post(
        f"{AGR_URL}/{agreement_id}/attachments",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )
    att_id = up.json()["id"]
    # The storage_key isn't returned directly, but download_url is built from
    # it (`{FILE_SERVER_URL}/files/{storage_key}`) — recover it so we can
    # assert the fake was asked to delete exactly that key, not just that
    # *some* delete call happened.
    storage_key = uuid.UUID(up.json()["download_url"].rsplit("/", 1)[-1])

    res = await admin_client.delete(f"{AGR_URL}/{agreement_id}/attachments/{att_id}")
    assert res.status_code == 204
    assert fake_file_server.deleted == [storage_key]

    listed = await admin_client.get(f"{AGR_URL}/{agreement_id}/attachments")
    assert listed.json() == []


async def test_upload_to_a_missing_agreement_is_404(admin_client):
    res = await admin_client.post(
        f"{AGR_URL}/00000000-0000-0000-0000-000000000000/attachments",
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
    )
    assert res.status_code == 404


async def test_download_returns_the_bytes(admin_client, agreement_id):
    up = await admin_client.post(
        f"{AGR_URL}/{agreement_id}/attachments",
        files={"file": ("x.pdf", b"%PDF-1.4 payload", "application/pdf")},
    )
    att_id = up.json()["id"]
    res = await admin_client.get(f"{AGR_URL}/{agreement_id}/attachments/{att_id}/download")
    assert res.status_code == 200
    assert res.content == b"%PDF-1.4 payload"


async def test_download_missing_attachment_is_404(admin_client, agreement_id):
    res = await admin_client.get(
        f"{AGR_URL}/{agreement_id}/attachments/{uuid.uuid4()}/download"
    )
    assert res.status_code == 404
