"""A person's files on an order or a transfer, and the transfer's order-like features (user 2026-11-13)."""
import pytest

from app.platform.commands import ANY_VERSION
from app.platform.errors import BusinessError
from app.services.record_files import Attachments
from app.services.transfer_views import TransferService
from tests.domain.test_transfers_edi import real_944
from tests.test_invoicing import p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, sent_transfer, store  # noqa: F401 — p7 is a fixture
from types import SimpleNamespace
import uuid

READER = SimpleNamespace(user_id=uuid.uuid4(), name="Reader", role="sales_reader", is_system=False)
PDF = b"%PDF-1.4 a delivery note"


async def test_a_file_attached_to_a_transfer_is_listed_viewed_and_removed_with_a_reason(p7):  # noqa: F811
    x = await sent_transfer(p7)
    async with p7.factory(OP) as u:
        a = await Attachments(u).attach("transfer", x.id, name="BOL scan.pdf", data=PDF, note="signed BOL")
        await u.commit()
    assert (a.kind, a.remote_name, a.note, a.size) == ("ATTACHMENT", "BOL scan.pdf", "signed BOL", len(PDF))
    async with p7.factory(OP) as u:
        d = await TransferService(u).detail(x.id)
        with pytest.raises(BusinessError) as e:  # the same file twice on one record
            await Attachments(u).attach("transfer", x.id, name="again.pdf", data=PDF, note=None)
    assert e.value.code == "attachment.already"
    assert [f.kind for f in d.documents] == ["943_EDI", "ATTACHMENT"] and d.attachments == 1
    [log] = await p7.q("SELECT message FROM sales_log WHERE step = 'attachment'")
    assert "BOL scan.pdf attached" in log["message"]
    async with p7.factory(OP) as u:
        with pytest.raises(BusinessError) as e:  # an EDI file is not a person's to remove
            await Attachments(u).remove("transfer", x.id, d.documents[0].id, "not wanted")
        assert e.value.code == "attachment.not_attachment"
        await Attachments(u).remove("transfer", x.id, a.id, "wrong transfer")
        await u.commit()
    async with p7.factory(OP) as u:
        assert await Attachments(u).list("transfer", x.id) == []
    assert await p7.q("SELECT count(*) n FROM sales_documents WHERE id = :i", i=a.id) == [{"n": 1}]  # kept, only unlinked


async def test_the_same_bytes_on_two_records_are_one_document(p6):  # noqa: F811
    o1 = await shipped_850(p6, "PO-A1")
    o2 = await shipped_850(p6, "PO-A2")
    async with p6.factory(OP) as u:
        a = await Attachments(u).attach("order", o1.id, name="pod.pdf", data=PDF, note=None)
        b = await Attachments(u).attach("order", o2.id, name="pod.pdf", data=PDF, note=None)
        await u.commit()
    assert a.id == b.id


async def test_refused_files_and_people(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-A3")
    async with p6.factory(OP) as u:
        for name, data, code in (("x.exe", b"MZ", "attachment.type"), ("empty.pdf", b"", "attachment.empty"),
                                 ("big.pdf", b"0" * (20 * 1024 * 1024 + 1), "attachment.too_large")):
            with pytest.raises(BusinessError) as e:
                await Attachments(u).attach("order", o.id, name=name, data=data, note=None)
            assert e.value.code == code
        with pytest.raises(BusinessError):
            await Attachments(u).attach("invoice", o.id, name="a.pdf", data=PDF, note=None)
    async with p6.factory(READER) as u:
        assert await Attachments(u).list("order", o.id) == []


async def test_a_transfer_on_hold_is_released_with_a_reason_and_shows_its_activity(p7):  # noqa: F811
    x = await sent_transfer(p7)
    await store(p7, "944_EDI", real_944(), "944-a.edi")
    await p7.tr.process_944s()
    second = real_944().replace(b"*16468", b"*16469").replace(b"W07*600*EA*628347940140", b"W07*500*EA*628347940140")
    await store(p7, "944_EDI", second, "944-b.edi")
    await p7.tr.process_944s()
    async with p7.factory(OP) as u:
        d = await TransferService(u).detail(x.id)
    assert d.status == "On Hold" and d.status_before_hold == "Discrepancy"
    async with p7.factory(OP) as u:
        out = await TransferService(u).release(x.id, "compared: the first 944 is right", ANY_VERSION)
        await u.commit()
    assert out.status == "Discrepancy"
    assert any(e.reason and "first 944 is right" in e.reason for e in out.timeline)
    async with p7.factory(OP) as u:
        with pytest.raises(BusinessError):
            await TransferService(u).release(x.id, "again", ANY_VERSION)
