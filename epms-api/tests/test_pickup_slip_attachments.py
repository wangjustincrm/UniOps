"""小票附件 —— 上传 / 列出 / 下载 / 删除。"""
import uuid

import pytest
from fastapi.responses import Response

from tests.test_agreements import AGR_URL, _agr_payload, seed_vendor_and_user
from tests.test_pickup_slip_api import _slip_payload, _slips_url

pytestmark = pytest.mark.asyncio


class _FakeFileServer:
    """In-memory stand-in for the file-api sidecar.

    The real file-api validates the caller's bearer token against its OWN
    JWT_SECRET_KEY (a live container's dev secret), which does not match this
    suite's standard JWT_SECRET_KEY=test-secret — hitting the real container
    made an earlier task's attachment tests fail under the documented test
    invocation and require a sidecar nobody else (including CI) has running.
    This fake keeps upload/download/delete deterministic and container-free,
    while still proving real behaviour: download round-trips the exact bytes
    that were uploaded, and delete records exactly which storage_key was
    asked for.
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
    `app.api.v1.agreement_slip_attachments.upload_to_file_server` etc. — not
    at their definition site in `app.services.attachment_helper`, since the
    router already bound the original names at import time.
    """
    fake = _FakeFileServer()
    monkeypatch.setattr("app.api.v1.agreement_slip_attachments.upload_to_file_server", fake.upload)
    monkeypatch.setattr("app.api.v1.agreement_slip_attachments.proxy_download", fake.download)
    monkeypatch.setattr("app.api.v1.agreement_slip_attachments.delete_from_file_server", fake.delete)
    return fake


@pytest.fixture
async def slip_id(admin_client, test_engine):
    vendor_id, _, user_id = await seed_vendor_and_user(test_engine)
    agr = (await admin_client.post(
        AGR_URL, json=_agr_payload(vendor_id, agreement_type="house_account"))).json()
    slip = (await admin_client.post(
        _slips_url(agr["id"]), json=_slip_payload(user_id))).json()
    return agr["id"], slip["id"]


async def test_upload_then_list_returns_the_file(admin_client, slip_id):
    agreement_id, sid = slip_id
    res = await admin_client.post(
        f"{_slips_url(agreement_id)}/{sid}/attachments",
        files={"file": ("receipt.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")},
    )
    assert res.status_code == 201, res.text
    assert res.json()["filename"] == "receipt.jpg"

    listed = await admin_client.get(f"{_slips_url(agreement_id)}/{sid}/attachments")
    assert [a["filename"] for a in listed.json()] == ["receipt.jpg"]


async def test_delete_removes_it(admin_client, slip_id, fake_file_server):
    agreement_id, sid = slip_id
    up = await admin_client.post(
        f"{_slips_url(agreement_id)}/{sid}/attachments",
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    att_id = up.json()["id"]
    # The storage_key isn't returned directly, but download_url is built from
    # it (`{FILE_SERVER_URL}/files/{storage_key}`) — recover it so we can
    # assert the fake was asked to delete exactly that key, not just that
    # *some* delete call happened.
    storage_key = uuid.UUID(up.json()["download_url"].rsplit("/", 1)[-1])

    res = await admin_client.delete(f"{_slips_url(agreement_id)}/{sid}/attachments/{att_id}")
    assert res.status_code == 204
    assert fake_file_server.deleted == [storage_key]

    listed = await admin_client.get(f"{_slips_url(agreement_id)}/{sid}/attachments")
    assert listed.json() == []


async def test_upload_to_a_missing_slip_is_404(admin_client, slip_id):
    agreement_id, _ = slip_id
    res = await admin_client.post(
        f"{_slips_url(agreement_id)}/{uuid.uuid4()}/attachments",
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert res.status_code == 404


async def test_download_returns_the_bytes(admin_client, slip_id):
    agreement_id, sid = slip_id
    up = await admin_client.post(
        f"{_slips_url(agreement_id)}/{sid}/attachments",
        files={"file": ("x.jpg", b"\xff\xd8\xff payload", "image/jpeg")},
    )
    att_id = up.json()["id"]
    res = await admin_client.get(f"{_slips_url(agreement_id)}/{sid}/attachments/{att_id}/download")
    assert res.status_code == 200
    assert res.content == b"\xff\xd8\xff payload"
