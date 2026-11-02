"""P7: transfers — the 943, its 997, the 944 (FR-OB-80 … 84; AC-OB-64 … 69,
75, 76) on the real pair of A.4 / A.7 (transfer 00104, 944 16468). P4's
fixture: writes ON, a LIVE warehouse connection that is a write host."""
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.domain.shadow import compare
from app.models.config import Item, Warehouse
from app.models.documents import Document
from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import Outbox, OutboxWorker
from app.schemas.transfers import TransferIn
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.transfer_views import TransferService
from app.services.transfers import SEND_943, Transfers
from tests.domain.test_transfers_edi import ZOHO_943, real_944
from tests.test_orders import OP, env  # noqa: F401 — env is a fixture (p4_edi needs it)
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

NOW = datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def p7(p4_edi):  # noqa: F811 — the P4 fixture, extended
    edi = p4_edi
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        s = u.session
        await s.execute(sa.text("TRUNCATE sales_receipt_rows, sales_receipts, sales_transfer_lines, sales_transfers, "
                                "sales_warehouses RESTART IDENTITY CASCADE"))
        await s.execute(sa.text("ALTER SEQUENCE sales_transfer_number_seq RESTART WITH 104"))  # the A.4 sample's number
        for code, w in (("CF0092", "2.27"), ("CF0093", "2.20"), ("CF0103", "2.20")):
            item = (await s.execute(sa.select(Item).where(Item.item_code == code))).scalar_one_or_none()
            if item is None:
                s.add(Item(item_code=code, sku=f"{code}-SKU", warehouse_held=True, gross_weight_lb_per_ea=Decimal(w), version=1))
            else:
                item.gross_weight_lb_per_ea = Decimal(w)
        s.add_all([
            Warehouse(code="KGN", name="Canada Royal Milk ULC", role="TRANSFER_SOURCE", edi_id_qualifier="94", edi_id="CRM",
                      address1="1680 Venture Dr", city="Kingston", province="ON", zip="K7P 0E9", country="CA", version=1),
            Warehouse(code="CLN02", name="Radiant Coleraine Warehouse", role="WAREHOUSE", edi_id_qualifier="94", edi_id="CLN02",
                      address1="12480 Coleraine Drive", city="Caledon", province="ON", zip="L7E 4M2", version=1)])
        await u.commit()
    tr = Transfers(uow_factory=edi.env.factory, tasks=edi.env.tasks, sftp=edi.gateways.sftp, clock=lambda: NOW)
    edi.out.transfers = tr
    worker = OutboxWorker(Outbox(OutboxRecord), {SEND_943: tr.outbox_kind()}, edi.env.factory)
    return SimpleNamespace(edi=edi, tr=tr, worker=worker, q=edi.env.q, factory=edi.env.factory, store=edi.store)


async def create(t, lines=(("CF0092", 8400), ("CF0093", 7200))):
    async with t.factory(OP) as u:
        out = await TransferService(u).create(t.tr, TransferIn(from_warehouse_id=1, to_warehouse_id=2, lines=[
            {"item_code": c, "qty_ea": q} for c, q in lines]))
        await u.commit()
        return out


async def act(t, name, id, *args):
    async with t.factory(OP) as u:
        out = await getattr(TransferService(u), name)(t.tr, id, *args)
        await u.commit()
        return out


async def status(t, id):
    return (await t.q("SELECT status FROM sales_transfers WHERE id = :i", i=id))[0]["status"]


async def tasks(t, type_):
    return [r["description"] for r in await t.q("SELECT description FROM tasks WHERE type = :t", t=f"sales_{type_}")]


async def sent_transfer(t, lines=(("CF0092", 8400), ("CF0093", 7200))):
    x = await create(t, lines)
    await act(t, "ready", x.id, None)
    await t.tr.queue_ready()
    await t.worker.run_once()
    return x


async def store(t, kind, data: bytes, name: str):
    async with t.factory(SYSTEM_ACTOR) as u:
        u.session.add(Document(direction="IN", origin="PARTNER", system="RADIANT", kind=kind, remote_name=name, size=len(data),
                               sha256=hashlib.sha256(data).hexdigest(), content=data, processing_status="Pending",
                               connection_id=t.edi.conn_id))
        await u.commit()


def ack997(control: str, accept=True, reasons="") -> bytes:
    a = "A" if accept else "R"
    return (f"ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*1500*U*00401*000016469*0*P*>~\n"
            f"GS*FA*9055651212*CANADAROYALMILK*20261102*1500*16469*X*006030~\nST*997*0001~\nAK1*OW*{int(control)}~\n"
            f"AK2*943*{control}~\nAK5*{a}{'*' + reasons if reasons else ''}~\nAK9*{a}*1*1*1~\nSE*6*0001~\nGE*1*16469~\n"
            f"IEA*1*000016469~\n").encode()


# ── the 943 (AC-OB-64) ───────────────────────────────────────────────────────

async def test_ac64_draft_then_ready_then_the_943(p7):
    x = await create(p7)
    assert x.transfer_number == "00104" and x.status == "Draft"
    assert (await p7.tr.queue_ready()).generated == 0 and p7.store.files == {}
    await act(p7, "ready", x.id, None)
    r = await p7.tr.queue_ready()
    assert r.generated == 1 and await status(p7, x.id) == "943 Generated"
    assert (await p7.worker.run_once()).done == 1 and await status(p7, x.id) == "Waiting for 997"
    [(path, data)] = p7.store.files.items()
    assert path.startswith("/prod/in/CRM-943_") and path.endswith("_TRID_00104_.EDI")
    c = compare(ZOHO_943, data)  # the real A.4 file: only the date and the file name (its control) differ
    assert [r["zoho"].split("*")[0] for r in c.rows if not r["same"]] == ["W06", "NTE"]


async def test_a_943_that_fails_its_checks_is_failed_with_a_task_and_resent_with_a_new_number(p7):
    async with p7.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(Item).where(Item.item_code == "CF0093"))).scalar_one().gross_weight_lb_per_ea = None
        await u.commit()
    x = await create(p7)
    await act(p7, "ready", x.id, None)
    assert (await p7.tr.queue_ready()).invalid == 1 and await status(p7, x.id) == "Failed"
    [t] = await tasks(p7, "transfer_failed")
    assert "CF0093 has no gross weight" in t
    async with p7.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(Item).where(Item.item_code == "CF0093"))).scalar_one().gross_weight_lb_per_ea = Decimal("2.2")
        await u.commit()
    await act(p7, "resend", x.id, None)
    assert await status(p7, x.id) == "943 Generated"
    ctls = [r["st02"] for r in await p7.q("SELECT st02 FROM sales_documents WHERE kind = '943_EDI' ORDER BY id")]
    assert len(set(ctls)) == 2


# ── the 997 (AC-OB-65) ───────────────────────────────────────────────────────

async def test_ac65_an_accepting_997_waits_for_the_944_a_rejecting_one_fails_with_the_ak5(p7):
    a = await sent_transfer(p7)
    [d] = await p7.q("SELECT st02 FROM sales_documents WHERE kind = '943_EDI'")
    await store(p7, "997_EDI", ack997(d["st02"]), "997-a.edi")
    await p7.edi.out.process_997s()
    assert await status(p7, a.id) == "Waiting for 944"
    b = await sent_transfer(p7, (("CF0092", 600),))
    [d] = await p7.q("SELECT st02 FROM sales_documents WHERE kind = '943_EDI' ORDER BY id DESC LIMIT 1")
    await store(p7, "997_EDI", ack997(d["st02"], accept=False, reasons="5"), "997-r.edi")
    await p7.edi.out.process_997s()
    assert await status(p7, b.id) == "Failed"
    [t] = await tasks(p7, "943_rejected")
    assert "AK5 R 5" in t and "not resent automatically" in t
    assert (await p7.tr.queue_ready()).generated == 0 and len(p7.store.files) == 2  # never resent by itself


# ── the 944 (AC-OB-66 / 67 / 68 / 75 / 76) ───────────────────────────────────

async def test_ac75_ac76_the_real_944_16468_against_transfer_00104(p7):
    x = await sent_transfer(p7)
    await store(p7, "944_EDI", real_944(), "944-16468.edi")  # before any 997: it counts as the acceptance
    r = await p7.tr.process_944s()
    assert r.receipts == 1 and await status(p7, x.id) == "Discrepancy"
    lines = await p7.q("SELECT line_no, item_code, qty_ea, received_ea, line_status FROM sales_transfer_lines ORDER BY line_no")
    assert [(ln["item_code"], ln["received_ea"], ln["line_status"]) for ln in lines] == [
        ("CF0092", 8400, "Received"), ("CF0093", 0, "Open")]                   # 14 × 600 summed (AC-OB-75)
    assert await p7.q("SELECT count(*) n FROM sales_receipt_rows") == [{"n": 26}]  # every pallet kept
    [t] = await tasks(p7, "transfer_discrepancy")
    assert "CF0103 7200 EA received but not on the transfer" in t and "line 2 CF0093: 0 of 7200 EA received" in t
    [d] = await p7.q("SELECT ack_status, ack_detail FROM sales_documents WHERE kind = '943_EDI'")
    assert d["ack_status"] == "Accepted" and "944" in d["ack_detail"]


async def test_ac66_ac67_received_partially_received_and_over_received(p7):
    def x944(number, rows):
        body = [f"W17*F*20261102*{number}*{number}", "G62*BS*20261102", *rows, f"W14*{sum(int(r.split('*')[1]) for r in rows if r.startswith('W07'))}"]
        return ("~\n".join(["ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*0624*U*00401*000016470*0*P*>",
                            "GS*RE*9055651212*CANADAROYALMILK*20261102*0624*16470*X*006030", "ST*944*16470", *body,
                            f"SE*{len(body) + 2}*16470", "GE*1*16470", "IEA*1*000016470"]) + "~\n").encode()
    full = await sent_transfer(p7, (("CF0092", 1200),))
    part = await sent_transfer(p7, (("CF0092", 1200),))
    over = await sent_transfer(p7, (("CF0092", 600),))
    pallet = ["LX*1", "W07*600*EA**VN*CF0092", "N9*LT*L1"]
    await store(p7, "944_EDI", x944(full.transfer_number, pallet * 2), "a.edi")
    await store(p7, "944_EDI", x944(part.transfer_number, pallet), "b.edi")
    await store(p7, "944_EDI", x944(over.transfer_number, pallet * 2), "c.edi")
    await p7.tr.process_944s()
    assert [await status(p7, i) for i in (full.id, part.id, over.id)] == ["Received", "Partially Received", "Discrepancy"]
    assert await p7.q("SELECT 1 FROM sales_log WHERE message LIKE 'transfer % partially received%'")
    [pr] = await tasks(p7, "partial_receipt")  # P8: notified (no rule: a task to the operator)
    assert f"transfer {part.transfer_number} received part of what was sent: line 1: 600 EA" in pr
    [t] = await tasks(p7, "transfer_discrepancy")
    assert "1200 EA received, 600 requested" in t


async def test_ac68_a_second_different_944_holds_and_the_same_one_changes_nothing(p7):
    x = await sent_transfer(p7)
    await store(p7, "944_EDI", real_944(), "944-16468.edi")
    await p7.tr.process_944s()
    assert (await p7.tr.process_944s()).receipts == 0  # applied once; nothing pending
    second = real_944().replace(b"W07*600*EA*628347940362*VN*CF0103", b"W07*600*EA*628347940362*VN*CF0093")
    await store(p7, "944_EDI", second, "944-16468-again.edi")
    r = await p7.tr.process_944s()
    assert r.second == 1 and await status(p7, x.id) == "On Hold"
    lines = await p7.q("SELECT received_ea FROM sales_transfer_lines ORDER BY line_no")
    assert [ln["received_ea"] for ln in lines] == [8400, 0]                     # unchanged
    [t] = await tasks(p7, "second_944")
    assert "944-16468.edi" in t and "944-16468-again.edi" in t


async def test_a_944_for_no_transfer_is_stored_with_a_task(p7):
    await store(p7, "944_EDI", real_944(), "944-orphan.edi")
    assert (await p7.tr.process_944s()).no_transfer == 1
    [t] = await tasks(p7, "944_no_transfer")
    assert "transfer 00104 is not in Sales" in t
    assert await p7.q("SELECT count(*) n FROM sales_receipt_rows") == [{"n": 26}]


# ── cancel (AC-OB-69) ────────────────────────────────────────────────────────

async def test_ac69_cancel_before_the_943_is_sent_or_after_a_rejection_only(p7):
    draft = await create(p7)
    assert (await act(p7, "cancel", draft.id, None, "not needed")).status == "Cancelled"
    queued = await create(p7)
    await act(p7, "ready", queued.id, None)
    await p7.tr.queue_ready()                                   # 943 Generated, row queued, not put
    assert (await act(p7, "cancel", queued.id, None, "wrong lines")).status == "Cancelled"
    assert (await p7.worker.run_once()).done == 0 and p7.store.files == {}
    sent = await sent_transfer(p7)
    with pytest.raises(BusinessError) as e:
        await act(p7, "cancel", sent.id, None, "too late")
    assert e.value.code == "transfer.not_cancellable"
    [d] = await p7.q("SELECT st02 FROM sales_documents WHERE kind = '943_EDI' AND file_status = 'Sent'")
    await store(p7, "997_EDI", ack997(d["st02"], accept=False, reasons="4"), "997-r.edi")
    await p7.edi.out.process_997s()
    assert (await act(p7, "cancel", sent.id, None, "rejected; not resending")).status == "Cancelled"


async def test_changes_need_the_transfer_permission(p7):
    from app.platform.authorization import PermissionDenied

    async def read_only(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    p7.factory.permissions = read_only
    with pytest.raises(PermissionDenied):
        await create(p7)


# ── the shadow run ───────────────────────────────────────────────────────────

async def test_shadow_no_943_is_sent_and_a_944_is_compared_never_applied(p7, tmp_path):
    x = await create(p7)
    await act(p7, "ready", x.id, None)
    await p7.tr.queue_ready()                                  # queued before the switch-on
    async with p7.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    assert (await p7.worker.run_once()).cancelled == 1 and p7.store.calls == []  # zero puts, writes ON and LIVE
    assert await status(p7, x.id) == "Failed" and await tasks(p7, "transfer_failed") == []
    y = await create(p7, (("CF0092", 600),))
    await act(p7, "ready", y.id, None)
    assert (await p7.tr.queue_ready()).skipped == "shadow run: no 943 is generated"
    from app.services.shadow import ShadowRun
    (tmp_path / "z943.edi").write_bytes(ZOHO_943)             # Zoho's 943 for 00104
    matcher = ShadowRun(uow_factory=p7.factory, outbound=p7.edi.out, reference_dir=str(tmp_path), clock=lambda: NOW)
    await matcher.run()
    p7.tr.shadow = matcher
    await store(p7, "944_EDI", real_944(), "944-16468.edi")
    assert (await p7.tr.process_944s()).shadow == 1
    assert [ln["received_ea"] for ln in await p7.q("SELECT received_ea FROM sales_transfer_lines WHERE transfer_id = :i", i=x.id)] == [0, 0]
    [m] = await p7.q("SELECT kind, status, would FROM sales_shadow_matches WHERE kind = '944'")
    assert (m["kind"], m["status"], m["would"]["outcome"]) == ("944", "Match", "Discrepancy")
    assert await p7.q("SELECT 1 FROM tasks") == []
    assert await p7.q("SELECT applied, shadow FROM sales_receipts") == [{"applied": False, "shadow": True}]
