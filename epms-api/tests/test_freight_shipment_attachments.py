"""Freight shipment detail + attachments (scanned shipping label, supporting files).

The File Server is replaced by an in-memory fake patched where the router
imported the helpers, so download proves the exact bytes round-trip.
"""
import uuid

import pytest
from fastapi.responses import Response

from tests.test_freight import _grant, _no_outbound_calls, _register, world  # noqa: F401

URL = "/api/v1/freight/shipments"


class _FakeFileServer:
    def __init__(self):
        self.store: dict[uuid.UUID, tuple[bytes, str]] = {}
        self.uploads: list[tuple[str, uuid.UUID]] = []
        self.deleted: list[uuid.UUID] = []

    async def upload(self, data, filename, content_type, doc_type, doc_id, bearer_token):
        key = uuid.uuid4()
        self.store[key] = (data, content_type)
        self.uploads.append((doc_type, doc_id))
        return key

    async def download(self, storage_key, bearer_token):
        data, content_type = self.store[storage_key]
        return Response(content=data, media_type=content_type)

    async def delete(self, storage_key, bearer_token):
        self.deleted.append(storage_key)
        self.store.pop(storage_key, None)


@pytest.fixture(autouse=True)
def fake_fs(monkeypatch):
    fake = _FakeFileServer()
    monkeypatch.setattr("app.api.v1.freight.upload_to_file_server", fake.upload)
    monkeypatch.setattr("app.api.v1.freight.proxy_download", fake.download)
    monkeypatch.setattr("app.api.v1.freight.delete_from_file_server", fake.delete)
    return fake


async def _shipment(client, w, tracking="1Z4E4F771000000001"):
    r = await _register(client, w, tracking)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_label_and_supporting_file_round_trip(admin_client, world, fake_fs):
    sid = await _shipment(admin_client, world)
    # The supporting file first: the label must still list first.
    r = await admin_client.post(f"{URL}/{sid}/attachments",
                                files={"file": ("pod.pdf", b"%PDF pod", "application/pdf")})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "other"
    r = await admin_client.post(f"{URL}/{sid}/attachments", data={"kind": "waybill"},
                                files={"file": ("label.jpg", b"\xff\xd8label", "image/jpeg")})
    assert r.status_code == 201, r.text
    label = r.json()
    assert label["kind"] == "waybill" and label["file_size"] == len(b"\xff\xd8label")
    assert fake_fs.uploads[-1] == ("freight_shipment", uuid.UUID(sid))

    atts = (await admin_client.get(f"{URL}/{sid}/attachments")).json()
    assert [a["filename"] for a in atts] == ["label.jpg", "pod.pdf"]

    dl = await admin_client.get(f"{URL}/{sid}/attachments/{label['id']}/download")
    assert dl.status_code == 200 and dl.content == b"\xff\xd8label"

    detail = (await admin_client.get(f"{URL}/{sid}")).json()
    assert detail["tracking_no"] == "1Z4E4F771000000001" and detail["attachment_count"] == 2
    listed = (await admin_client.get(f"{URL}?search=1Z4E4F771000000001")).json()["items"]
    assert listed[0]["attachment_count"] == 2

    assert (await admin_client.delete(f"{URL}/{sid}/attachments/{label['id']}")).status_code == 204
    assert len(fake_fs.deleted) == 1
    assert [a["filename"] for a in (await admin_client.get(f"{URL}/{sid}/attachments")).json()] \
        == ["pod.pdf"]


async def test_bad_kind_and_wrong_shipment_are_refused(admin_client, world):
    sid = await _shipment(admin_client, world)
    other = await _shipment(admin_client, world, "1Z4E4F771000000002")
    r = await admin_client.post(f"{URL}/{sid}/attachments", data={"kind": "invoice"},
                                files={"file": ("x.pdf", b"x", "application/pdf")})
    assert r.status_code == 422
    att = (await admin_client.post(f"{URL}/{sid}/attachments",
                                   files={"file": ("x.pdf", b"x", "application/pdf")})).json()
    # An attachment is only reachable under its own shipment.
    assert (await admin_client.get(f"{URL}/{other}/attachments/{att['id']}/download")
            ).status_code == 404
    assert (await admin_client.delete(f"{URL}/{other}/attachments/{att['id']}")).status_code == 404


async def test_void_shipment_files_are_read_only(admin_client, world):
    sid = await _shipment(admin_client, world)
    att = (await admin_client.post(f"{URL}/{sid}/attachments",
                                   files={"file": ("x.pdf", b"x", "application/pdf")})).json()
    assert (await admin_client.post(f"{URL}/{sid}/void")).status_code == 200
    assert (await admin_client.post(f"{URL}/{sid}/attachments",
                                    files={"file": ("y.pdf", b"y", "application/pdf")})
            ).status_code == 409
    assert (await admin_client.delete(f"{URL}/{sid}/attachments/{att['id']}")).status_code == 409
    # Still readable.
    assert (await admin_client.get(f"{URL}/{sid}/attachments/{att['id']}/download")
            ).status_code == 200


async def test_registrar_reaches_own_shipment_files_only(test_engine, admin_client, world):
    from tests.conftest import _authenticated_client
    await _grant(test_engine, "procurement_officer", "epms.freight.register")
    theirs = await _shipment(admin_client, world, "1Z4E4F771000000003")
    async with await _authenticated_client(test_engine, "procurement_officer") as buyer:
        mine = await _shipment(buyer, world, "1Z4E4F771000000004")
        # Admission: their own shipment's detail and files.
        assert (await buyer.get(f"{URL}/{mine}")).status_code == 200
        r = await buyer.post(f"{URL}/{mine}/attachments", data={"kind": "waybill"},
                             files={"file": ("l.png", b"png", "image/png")})
        assert r.status_code == 201, r.text
        assert (await buyer.get(f"{URL}/{mine}/attachments")).status_code == 200
        # Denial: someone else's shipment is not found.
        assert (await buyer.get(f"{URL}/{theirs}")).status_code == 404
        assert (await buyer.get(f"{URL}/{theirs}/attachments")).status_code == 404
        assert (await buyer.post(f"{URL}/{theirs}/attachments",
                                 files={"file": ("x.pdf", b"x", "application/pdf")})
                ).status_code == 404
    async with await _authenticated_client(test_engine, "warehouse_staff") as nobody:
        assert (await nobody.get(f"{URL}/{mine}/attachments")).status_code == 403
