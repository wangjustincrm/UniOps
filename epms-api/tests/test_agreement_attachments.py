"""协议附件 —— 上传 / 列出 / 下载 / 删除。"""
import uuid

import pytest

from tests.test_agreements import AGR_URL, _agr_payload, seed_vendor_and_user

pytestmark = pytest.mark.asyncio


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


async def test_delete_removes_it(admin_client, agreement_id):
    up = await admin_client.post(
        f"{AGR_URL}/{agreement_id}/attachments",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )
    att_id = up.json()["id"]
    res = await admin_client.delete(f"{AGR_URL}/{agreement_id}/attachments/{att_id}")
    assert res.status_code == 204
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
