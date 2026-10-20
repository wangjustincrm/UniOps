"""Click-to-sort on the EPMS procurement lists: GET /pr, /po, /pa, /agreements,
/gr, /gr/receiving-report and /agreement-receipts take `sort` + `dir`.

These lists are paged by the API, so the API must sort — sorting the 20 rows
on screen was wrong. Each endpoint is checked for: asc/desc order, NULLs last
in BOTH directions, unknown key → 422, no sort → the old default order, and
page 2 continuing page 1's order.

The Status column of PR/PO/PA is the one with real logic: status, then (in
review only) the current approval step in chain order (ROLE_ORDER), then the
approver's name, named before the role pool. The current step is the earliest
open approve_<doc> task — the same one enrich_current_step shows.

Every test seeds rows tagged with its own token and narrows the list to them
(search / agreement filter), because the session-wide test DB keeps other
tests' rows. Every row a test inserts is deleted again afterwards (see
_cleanup_seeded_rows): later files assert on whole-table counts — e.g.
test_admin's PR cascade test expects purchase_requests to end up empty.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_receipt import AgreementReceipt
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.pa import PaymentApplication
from app.models.pa_po_link import PaPoLink
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _t(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


# Every object these tests' sessions insert, in insertion order.
_SEEDED: list = []


class _TrackingSession(Session):
    """Records what it inserts, so the fixture below can delete it again."""


@event.listens_for(_TrackingSession, "after_flush")
def _record_inserts(session, _ctx):
    # after_flush still sees the pre-flush `new` set, now with ids assigned.
    _SEEDED.extend(session.new)


@pytest.fixture(autouse=True)
async def _cleanup_seeded_rows(test_engine):
    _SEEDED.clear()
    yield
    # Reverse insertion order: children (tasks, links, lines, documents) go
    # before the parents they reference (documents, vendors, users).
    seen = set()
    async with test_engine.begin() as conn:
        for obj in reversed(_SEEDED):
            table = type(obj).__table__
            key = (table.name, obj.id)
            if key in seen:
                continue
            seen.add(key)
            await conn.execute(delete(table).where(table.c.id == obj.id))
    _SEEDED.clear()


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False,
                              sync_session_class=_TrackingSession)


def _tok() -> str:
    # A non-digit inside keeps _numbering.next_number from reading it as a serial.
    return "s" + uuid.uuid4().hex[:7] + "x"


async def _user(db, full_name: str):
    u = await user_crud.create(db, RegisterRequest(
        email=f"sort-{uuid.uuid4().hex[:10]}@t.com", password="TestPass1!",
        full_name=full_name, role="system_admin"))
    await db.flush()
    return u


async def _vendor(db, name: str = "Sort Vendor"):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name=name, category="Services",
               contact_name="S", contact_email="s@sort.test")
    db.add(v)
    await db.flush()
    return v


async def _ids(client, url, **params):
    r = await client.get(url, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _order(body, idkey="id"):
    return [it[idkey] for it in body["items"]]


async def _check_paging(client, url, expected, **params):
    """page 2 continues page 1 in the same order (sort pushed into SQL)."""
    got, page = [], 1
    while True:
        body = await _ids(client, url, page=page, page_size=2, **params)
        assert body["total"] == len(expected)
        if not body["items"]:
            break
        got += _order(body)
        page += 1
    assert page > 2                       # really spanned several pages
    assert got == expected


# ════════════════════════════════════════════════════════════════════════════
# Status column — status → step (chain order) → approver name
# ════════════════════════════════════════════════════════════════════════════

# (label, status, open tasks as (role, approver-name|None, minutes), extra)
#   B/C/D share the supervisor step: Alice, bob (case-insensitive), then the pool.
#   F is approved but still has a stale open task — it must not matter.
#   H has two open tasks; the EARLIEST (finance_bp) is its current step, and a
#   completed supervisor task must be ignored.
_STATUS_CASES = [
    ("A", "in_review", [("director", "Zed Approver", 5)]),
    ("B", "in_review", [("supervisor", "bob Approver", 5)]),
    ("C", "in_review", [("supervisor", "Alice Approver", 5)]),
    ("D", "in_review", [("supervisor", None, 5)]),
    ("E", "in_review", []),
    ("F", "approved", [("supervisor", "Aaron Stale", 5)]),
    ("G", "draft", []),
    ("H", "in_review", [("finance_bp", "Yan Approver", 1), ("supervisor", "Aa First", 9)]),
]
_STATUS_ASC = ["F", "G", "C", "B", "D", "A", "H", "E"]
_STATUS_DESC = ["H", "A", "B", "C", "D", "E", "G", "F"]   # no-step E stays last
_EXPECTED_STEP = {"A": "director", "B": "supervisor", "C": "supervisor",
                  "D": "supervisor", "H": "finance_bp"}


async def _make_doc(db, doc_type, tok, label, status, creator, vendor, i):
    """One PR / PO / PA titled/vendored with the token so search narrows to it."""
    if doc_type == "pr":
        doc = PurchaseRequest(number=f"PR-{tok}-{label}", title=f"{tok} {label}", type=2,
                              status=status, created_by=creator.id, created_at=_t(i))
        db.add(doc)
    elif doc_type == "po":
        doc = PurchaseOrder(number=f"PO-{tok}-{label}", title=f"{tok} {label}", type=2,
                            status=status, vendor_id=vendor.id, vendor_name=vendor.name,
                            created_by=creator.id, created_at=_t(i))
        db.add(doc)
    else:
        po = PurchaseOrder(number=f"PO-{tok}-{label}", title=f"{tok} {label}", type=2,
                           status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
                           created_by=creator.id)
        db.add(po)
        await db.flush()
        doc = PaymentApplication(pa_number=f"PA-{tok}-{label}", title=f"{tok} {label}",
                                 po_id=po.id, po_number=po.number, vendor_id=vendor.id,
                                 vendor_name=vendor.name, subtotal=Decimal("1"),
                                 payment_amount=Decimal("1"), status=status,
                                 created_by=creator.id, created_at=_t(i))
        db.add(doc)
    await db.flush()
    return doc


async def _seed_status(test_engine, doc_type):
    tok = _tok()
    ids = {}
    async with _factory(test_engine)() as db:
        creator = await _user(db, "Creator")
        vendor = await _vendor(db, f"{tok} Vendor")
        approvers = {}
        for i, (label, status, tasks) in enumerate(_STATUS_CASES):
            doc = await _make_doc(db, doc_type, tok, label, status, creator, vendor, i)
            ids[label] = str(doc.id)
            for role, name, minutes in tasks:
                uid = None
                if name:
                    if name not in approvers:
                        approvers[name] = await _user(db, name)
                    uid = approvers[name].id
                db.add(Task(type=f"approve_{doc_type}", document_type=doc_type,
                            document_id=doc.id, document_number=f"{tok}-{label}",
                            assigned_role=role, assigned_user_id=uid, title="Approve",
                            is_completed=False, created_at=_t(minutes)))
            if label == "H":   # a completed earlier task never counts
                db.add(Task(type=f"approve_{doc_type}", document_type=doc_type,
                            document_id=doc.id, document_number=f"{tok}-{label}",
                            assigned_role="supervisor", title="Approve",
                            is_completed=True, created_at=_t(0)))
        await db.commit()
    return tok, ids


_LIST_URL = {"pr": "/api/v1/pr", "po": "/api/v1/po", "pa": "/api/v1/pa"}


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["pr", "po", "pa"])
async def test_status_sort_follows_status_then_step_then_approver(admin_client, test_engine, doc_type):
    tok, ids = await _seed_status(test_engine, doc_type)
    url = _LIST_URL[doc_type]
    label_of = {v: k for k, v in ids.items()}

    asc = await _ids(admin_client, url, search=tok, sort="status", dir="asc", page_size=50)
    assert [label_of[i] for i in _order(asc)] == _STATUS_ASC

    desc = await _ids(admin_client, url, search=tok, sort="status", dir="desc", page_size=50)
    assert [label_of[i] for i in _order(desc)] == _STATUS_DESC

    # The step the list is sorted by is the step the row shows.
    for it in asc["items"]:
        want = _EXPECTED_STEP.get(label_of[it["id"]])
        got = (it.get("current_step") or {}).get("role")
        assert got == want, (label_of[it["id"]], got, want)

    # Sorted across pages, not per page.
    await _check_paging(admin_client, url, [ids[x] for x in _STATUS_ASC],
                        search=tok, sort="status", dir="asc")


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["pr", "po", "pa"])
async def test_status_lists_unknown_key_and_default(admin_client, test_engine, doc_type):
    tok, ids = await _seed_status(test_engine, doc_type)
    url = _LIST_URL[doc_type]
    r = await admin_client.get(url, params={"search": tok, "sort": "nope"})
    assert r.status_code == 422
    r = await admin_client.get(url, params={"search": tok, "sort": "status", "dir": "sideways"})
    assert r.status_code == 422
    # No sort → created_at desc, as before.
    body = await _ids(admin_client, url, search=tok, page_size=50)
    assert _order(body) == [ids[c[0]] for c in reversed(_STATUS_CASES)]


# Open approval tasks carry the RESOLVED post: a gm_or_opm step is assigned as
# gm or opm, and older PAs still have an ap_clerk review step. Each must group
# at its own place in the chain (ROLE_ORDER), not fall into "unknown".
_RESOLVED_POSTS = [("p_opm", "opm"), ("p_gm", "gm"), ("p_ap", "ap_clerk"),
                   ("p_fbp", "finance_bp"), ("p_none", None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["po", "pa"])
async def test_status_sort_groups_resolved_posts_by_step(admin_client, test_engine, doc_type):
    tok = _tok()
    ids = {}
    async with _factory(test_engine)() as db:
        creator = await _user(db, "Creator")
        approver = await _user(db, "Same Approver")   # one name: only the step differs
        vendor = await _vendor(db, f"{tok} Vendor")
        # Insert in reverse chain order so creation order cannot fake the result.
        for i, (label, role) in enumerate(reversed(_RESOLVED_POSTS)):
            doc = await _make_doc(db, doc_type, tok, label, "in_review", creator, vendor, i)
            ids[label] = str(doc.id)
            if role:
                db.add(Task(type=f"approve_{doc_type}", document_type=doc_type,
                            document_id=doc.id, document_number=f"{tok}-{label}",
                            assigned_role=role, assigned_user_id=approver.id, title="Approve",
                            is_completed=False, created_at=_t(5)))
        await db.commit()
    url = _LIST_URL[doc_type]
    label_of = {v: k for k, v in ids.items()}
    chain = [label for label, _ in _RESOLVED_POSTS]

    asc = await _ids(admin_client, url, search=tok, sort="status", dir="asc")
    assert [label_of[i] for i in _order(asc)] == chain     # opm < gm < ap_clerk < finance_bp < no task
    assert [(it.get("current_step") or {}).get("role") for it in asc["items"]] == \
        [role for _, role in _RESOLVED_POSTS]

    # desc reverses the known steps; the no-task document has no step, so it
    # stays last in both directions (UI-SORT-003).
    desc = await _ids(admin_client, url, search=tok, sort="status", dir="desc")
    assert [label_of[i] for i in _order(desc)] == list(reversed(chain[:-1])) + chain[-1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["pr", "po"])
async def test_number_sorts_trimmed_and_case_insensitive(admin_client, test_engine, doc_type):
    """A leading space or a lowercase prefix must not jump to the top."""
    tok = _tok()
    pfx = doc_type.upper()
    numbers = {"a": f"{doc_type}-{tok}-a",        # lowercase
               "b": f" {pfx}-{tok}-b",           # leading space
               "c": f"{pfx}-{tok}-c"}
    ids = {}
    async with _factory(test_engine)() as db:
        who = await _user(db, "Num Creator")
        vendor = await _vendor(db)
        for i, (lbl, num) in enumerate(numbers.items()):
            if doc_type == "pr":
                doc = PurchaseRequest(number=num, title=f"{tok} {lbl}", type=2,
                                      created_by=who.id, created_at=_t(i))
            else:
                doc = PurchaseOrder(number=num, title=f"{tok} {lbl}", type=2, vendor_id=vendor.id,
                                    vendor_name=vendor.name, created_by=who.id, created_at=_t(i))
            db.add(doc)
            await db.flush()
            ids[lbl] = str(doc.id)
        await db.commit()
    url = _LIST_URL[doc_type]
    rev = {v: k for k, v in ids.items()}
    asc = await _ids(admin_client, url, search=tok, sort="number", dir="asc")
    assert [rev[i] for i in _order(asc)] == ["a", "b", "c"]
    desc = await _ids(admin_client, url, search=tok, sort="number", dir="desc")
    assert [rev[i] for i in _order(desc)] == ["c", "b", "a"]


# ════════════════════════════════════════════════════════════════════════════
# PR — the other columns
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pr_columns(admin_client, test_engine):
    tok = _tok()
    async with _factory(test_engine)() as db:
        zoe, amy = await _user(db, "zoe Requester"), await _user(db, "Amy Requester")
        bea = await _user(db, "Bea Requester")
        rows = [
            # label, title, creator, vendor_name, amount, type, prepaid, submitted
            ("a", f"{tok} beta", zoe, "Zeta Co", Decimal("100"), 6, False, _t(30)),
            ("b", f"{tok} Alpha", amy, "acme", Decimal("9.5"), 3, True, None),
            ("c", f"{tok} gamma", bea, None, Decimal("1000"), 2, False, _t(10)),
        ]
        ids = {}
        for i, (lbl, title, who, vn, amt, typ, pre, sub) in enumerate(rows):
            pr = PurchaseRequest(number=f"PR-{tok}-{lbl}", title=title, type=typ,
                                 status="draft", created_by=who.id, vendor_name=vn,
                                 amount=amt, is_prepaid=pre, submitted_at=sub,
                                 created_at=_t(i))
            db.add(pr)
            await db.flush()
            ids[lbl] = str(pr.id)
        await db.commit()
    url = "/api/v1/pr"

    async def order(key, d):
        body = await _ids(admin_client, url, search=tok, sort=key, dir=d)
        return ["abc"[[ids["a"], ids["b"], ids["c"]].index(i)] for i in _order(body)]

    assert await order("title", "asc") == ["b", "a", "c"]          # case-insensitive
    assert await order("title", "desc") == ["c", "a", "b"]
    assert await order("created_by_name", "asc") == ["b", "c", "a"]  # Amy, Bea, zoe (by name)
    assert await order("created_by_name", "desc") == ["a", "c", "b"]
    assert await order("amount", "asc") == ["b", "a", "c"]          # numeric, not text
    assert await order("amount", "desc") == ["c", "a", "b"]
    # Type sorts by the LABEL: Consumables(2) < Project(6) < Spare Parts(3)
    assert await order("type", "asc") == ["c", "a", "b"]
    assert (await order("is_prepaid", "desc"))[0] == "b"
    # NULLs last in both directions.
    assert await order("vendor_name", "asc") == ["b", "a", "c"]
    assert await order("vendor_name", "desc") == ["a", "b", "c"]
    assert await order("submitted_at", "asc") == ["c", "a", "b"]
    assert await order("submitted_at", "desc") == ["a", "c", "b"]
    assert await order("number", "desc") == ["c", "b", "a"]
    await _check_paging(admin_client, url, [ids["b"], ids["a"], ids["c"]],
                        search=tok, sort="amount", dir="asc")


# ════════════════════════════════════════════════════════════════════════════
# PO — the other columns
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_po_columns(admin_client, test_engine):
    tok = _tok()
    async with _factory(test_engine)() as db:
        who = await _user(db, "PO Creator")
        vendor = await _vendor(db)
        pr = PurchaseRequest(number=f"PR-{tok}-z", title="pr", type=2, created_by=who.id)
        db.add(pr)
        await db.flush()
        rows = [
            # label, title, vendor_name, total, type, expected_delivery, pr?
            ("a", f"{tok} Beta", "zed", Decimal("50"), 5, date(2026, 3, 1), True),
            ("b", f"{tok} alpha", "Acme", Decimal("7"), 1, None, False),
            ("c", f"{tok} Gamma", "bolt", Decimal("700"), 4, date(2026, 2, 1), False),
        ]
        ids = {}
        for i, (lbl, title, vn, total, typ, exp, linked) in enumerate(rows):
            po = PurchaseOrder(number=f"PO-{tok}-{lbl}", title=title, type=typ, status="issued",
                               vendor_id=vendor.id, vendor_name=vn, total=total,
                               expected_delivery=exp, created_by=who.id, created_at=_t(i),
                               pr_id=pr.id if linked else None,
                               pr_number=pr.number if linked else None)
            db.add(po)
            await db.flush()
            ids[lbl] = str(po.id)
        await db.commit()
    url = "/api/v1/po"
    rev = {v: k for k, v in ids.items()}

    async def order(key, d):
        body = await _ids(admin_client, url, search=tok, sort=key, dir=d)
        return [rev[i] for i in _order(body)]

    assert await order("title", "asc") == ["b", "a", "c"]
    assert await order("vendor_name", "asc") == ["b", "c", "a"]
    assert await order("total", "desc") == ["c", "a", "b"]
    # Labels: Fixed Asset(5) < Raw Mat./Pack.(1) < Service(4)
    assert await order("type", "asc") == ["a", "b", "c"]
    assert await order("expected_delivery", "asc") == ["c", "a", "b"]
    assert await order("expected_delivery", "desc") == ["a", "c", "b"]
    assert (await order("pr_number", "asc"))[0] == "a"
    assert (await order("pr_number", "desc"))[0] == "a"           # one value, the rest NULL last
    assert await order("created_at", "asc") == ["a", "b", "c"]
    await _check_paging(admin_client, url, [ids["c"], ids["a"], ids["b"]],
                        search=tok, sort="total", dir="desc")


# ════════════════════════════════════════════════════════════════════════════
# PA — the other columns
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pa_columns(admin_client, test_engine):
    tok = _tok()
    async with _factory(test_engine)() as db:
        who = await _user(db, "PA Creator")
        vendor = await _vendor(db)
        agr = PurchaseAgreement(number=f"AGR-{tok}", title="agr", agreement_type="house_account",
                                vendor_id=vendor.id, vendor_name=vendor.name,
                                valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
                                created_by=who.id)
        db.add(agr)
        po_m = PurchaseOrder(number=f"PO-{tok}-M", title="m", type=2, vendor_id=vendor.id,
                             vendor_name=vendor.name, created_by=who.id)
        po_b = PurchaseOrder(number=f"PO-{tok}-B", title="b", type=2, vendor_id=vendor.id,
                             vendor_name=vendor.name, created_by=who.id)
        db.add_all([po_m, po_b])
        await db.flush()
        pa_a = PaymentApplication(pa_number=f"PA-{tok}-a", title="a", po_id=po_m.id,
                                  po_number=po_m.number, vendor_id=vendor.id,
                                  vendor_name=f"{tok} zulu", subtotal=Decimal("1"),
                                  payment_amount=Decimal("300"), pa_type="settlement",
                                  created_by=who.id, created_at=_t(0))
        # Header says PO-M, but its FIRST linked PO is PO-B — the cell shows PO-B.
        pa_b = PaymentApplication(pa_number=f"PA-{tok}-b", title="b", po_id=po_m.id,
                                  po_number=po_m.number, vendor_id=vendor.id,
                                  vendor_name=f"{tok} Alpha", subtotal=Decimal("1"),
                                  payment_amount=Decimal("40"), pa_type="balance",
                                  created_by=who.id, created_at=_t(1))
        # Agreement PA: no PO shown → NULL last.
        pa_c = PaymentApplication(pa_number=f"PA-{tok}-c", title="c", agreement_id=agr.id,
                                  agreement_number=agr.number, vendor_id=vendor.id,
                                  vendor_name=f"{tok} mike", subtotal=Decimal("1"),
                                  payment_amount=Decimal("5"), pa_type="regular",
                                  created_by=who.id, created_at=_t(2))
        db.add_all([pa_a, pa_b, pa_c])
        await db.flush()
        db.add_all([
            PaPoLink(pa_id=pa_b.id, po_id=po_b.id, po_number=po_b.number, sort_order=0),
            PaPoLink(pa_id=pa_b.id, po_id=po_m.id, po_number=po_m.number, sort_order=1),
        ])
        await db.commit()
        ids = {"a": str(pa_a.id), "b": str(pa_b.id), "c": str(pa_c.id)}
    url = "/api/v1/pa"
    rev = {v: k for k, v in ids.items()}

    async def order(key, d):
        body = await _ids(admin_client, url, search=tok, sort=key, dir=d)
        return [rev[i] for i in _order(body)]

    assert await order("vendor_name", "asc") == ["b", "c", "a"]
    assert await order("payment_amount", "asc") == ["c", "b", "a"]
    assert await order("pa_type", "asc") == ["b", "c", "a"]
    assert await order("po_number", "asc") == ["b", "a", "c"]
    assert await order("po_number", "desc") == ["a", "b", "c"]
    assert await order("pa_number", "desc") == ["c", "b", "a"]
    assert await order("created_at", "desc") == ["c", "b", "a"]
    await _check_paging(admin_client, url, [ids["c"], ids["b"], ids["a"]],
                        search=tok, sort="payment_amount", dir="asc")


# ════════════════════════════════════════════════════════════════════════════
# Agreements
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_agreement_columns(admin_client, test_engine):
    tok = _tok()
    async with _factory(test_engine)() as db:
        who = await _user(db, "Agr Creator")
        vendor = await _vendor(db)
        rows = [
            ("a", "beta", "recurring", "Zed", date(2026, 3, 1), date(2026, 9, 1), "200", "active"),
            ("b", "Alpha", "house_account", "acme", date(2026, 1, 1), date(2026, 12, 1), "5", "draft"),
            ("c", "gamma", "milestone", "Bolt", date(2026, 2, 1), date(2026, 6, 1), "40", "closed"),
        ]
        ids = {}
        for i, (lbl, title, typ, vn, vf, vt, consumed, st) in enumerate(rows):
            a = PurchaseAgreement(number=f"AGR-{tok}-{lbl}", title=f"{tok} {title}",
                                  agreement_type=typ, vendor_id=vendor.id, vendor_name=vn,
                                  valid_from=vf, valid_to=vt, consumed_amount=Decimal(consumed),
                                  status=st, created_by=who.id, created_at=_t(i))
            db.add(a)
            await db.flush()
            ids[lbl] = str(a.id)
        await db.commit()
    url = "/api/v1/agreements"
    rev = {v: k for k, v in ids.items()}

    async def order(key, d):
        body = await _ids(admin_client, url, search=tok, sort=key, dir=d)
        return [rev[i] for i in _order(body)]

    assert await order("title", "asc") == ["b", "a", "c"]
    assert await order("vendor_name", "desc") == ["a", "c", "b"]
    assert await order("agreement_type", "asc") == ["b", "c", "a"]
    assert await order("valid_from", "asc") == ["b", "c", "a"]
    assert await order("valid_to", "desc") == ["b", "a", "c"]
    assert await order("consumed_amount", "asc") == ["b", "c", "a"]   # numeric
    assert await order("status", "asc") == ["a", "c", "b"]
    assert await order("number", "desc") == ["c", "b", "a"]
    assert (await admin_client.get(url, params={"sort": "nope"})).status_code == 422
    body = await _ids(admin_client, url, search=tok)
    assert _order(body) == [ids["c"], ids["b"], ids["a"]]               # default created_at desc
    await _check_paging(admin_client, url, [ids["b"], ids["c"], ids["a"]],
                        search=tok, sort="consumed_amount", dir="asc")


# ════════════════════════════════════════════════════════════════════════════
# GR list + receiving report
# ════════════════════════════════════════════════════════════════════════════

async def _seed_grs(test_engine, tok):
    async with _factory(test_engine)() as db:
        who = await _user(db, "GR Creator")
        zed, amy = await _user(db, "zed Requester"), await _user(db, "Amy Requester")
        vendor = await _vendor(db)
        ids = {}
        rows = [
            # label, vendor_name, gr_type, status, received_at, received_by, collected_at,
            #   line totals, requester, department, placed_at
            ("a", f"{tok} Zulu", "physical", "pending_ack", _t(300), "bob", None,
             ["10", "15"], zed, "Ops", _t(-1440 * 3)),
            ("b", f"{tok} alpha", "physical", "confirmed", _t(100), None, _t(200),
             ["300"], amy, "Admin", _t(-1440 * 10)),
            ("c", f"{tok} Mike", "physical", "discrepancy", _t(200), "Alice", _t(400),
             ["2"], None, None, None),
        ]
        for i, (lbl, vn, gtype, st, rec, rby, col, totals, req, dept, placed) in enumerate(rows):
            pr = None
            if req is not None:
                pr = PurchaseRequest(number=f"PR-{tok}-{lbl}", title="pr", type=2,
                                     created_by=req.id, department_name=dept)
                db.add(pr)
                await db.flush()
            po = PurchaseOrder(number=f"PO-{tok}-{lbl}", title="po", type=2, status="issued",
                               vendor_id=vendor.id, vendor_name=vn, created_by=who.id,
                               placed_at=placed, pr_id=pr.id if pr else None,
                               created_at=_t(-1440 * 20))
            db.add(po)
            await db.flush()
            gr = GoodsReceipt(number=f"GR-{tok}-{lbl}", title="gr", po_id=po.id,
                              po_number=po.number, pr_id=pr.id if pr else None,
                              vendor_id=vendor.id, vendor_name=vn, gr_type=gtype,
                              procurement_type=2, status=st, received_at=rec, received_by=rby,
                              collected_at=col, created_by=who.id, created_at=_t(i))
            db.add(gr)
            await db.flush()
            for j, tot in enumerate(totals):
                db.add(GrLineItem(gr_id=gr.id, description=f"{lbl} item {j}",
                                  qty_ordered=Decimal("1"), qty_received=Decimal(str(j + 1 + i * 10)),
                                  unit="EA", unit_price=Decimal(tot), line_total=Decimal(tot),
                                  sort_order=j))
            ids[lbl] = str(gr.id)
        await db.commit()
    return ids


@pytest.mark.asyncio
async def test_gr_columns(admin_client, test_engine):
    tok = _tok()
    ids = await _seed_grs(test_engine, tok)
    url = "/api/v1/gr"
    rev = {v: k for k, v in ids.items()}

    async def order(key, d):
        body = await _ids(admin_client, url, search=tok, sort=key, dir=d)
        return [rev[i] for i in _order(body)]

    assert await order("vendor_name", "asc") == ["b", "c", "a"]
    assert await order("status", "asc") == ["b", "c", "a"]
    assert await order("received_at", "desc") == ["a", "c", "b"]
    assert await order("total_value", "asc") == ["c", "a", "b"]     # 2 < 25 < 300
    assert await order("received_by", "asc") == ["c", "a", "b"]     # Alice, bob, NULL
    assert await order("received_by", "desc") == ["a", "c", "b"]    # NULL still last
    assert await order("number", "asc") == ["a", "b", "c"]
    assert await order("po_number", "desc") == ["c", "b", "a"]
    assert len(await order("gr_type", "asc")) == 3
    assert (await admin_client.get(url, params={"sort": "nope"})).status_code == 422
    body = await _ids(admin_client, url, search=tok)
    assert _order(body) == [ids["c"], ids["b"], ids["a"]]           # default created_at desc
    await _check_paging(admin_client, url, [ids["c"], ids["a"], ids["b"]],
                        search=tok, sort="total_value", dir="asc")


@pytest.mark.asyncio
async def test_receiving_report_columns(admin_client, test_engine):
    tok = _tok()
    ids = await _seed_grs(test_engine, tok)
    url = "/api/v1/gr/receiving-report"

    async def order(key, d):
        body = await _ids(admin_client, url, search=tok, sort=key, dir=d)
        return [it["description"] for it in body["items"]]

    async def letters(key, d):
        # GR "a" has two lines that tie on every GR-level column; their mutual
        # order is the line-id tiebreak, so compare by GR only.
        return [x[0] for x in await order(key, d)]

    # Default: received_at asc, then GR number, then line order.
    body = await _ids(admin_client, url, search=tok)
    assert [it["description"] for it in body["items"]] == \
        ["b item 0", "c item 0", "a item 0", "a item 1"]
    assert await order("quantity", "desc") == ["c item 0", "b item 0", "a item 1", "a item 0"]
    assert await order("quantity", "asc") == ["a item 0", "a item 1", "b item 0", "c item 0"]
    assert await letters("supplier", "asc") == ["b", "c", "a", "a"]
    assert await letters("requested_by", "asc") == ["b", "a", "a", "c"]    # Amy, zed, NULL
    assert await letters("requested_by", "desc") == ["a", "a", "b", "c"]   # NULL still last
    assert await letters("department", "asc") == ["b", "a", "a", "c"]      # Admin, Ops, NULL
    assert await letters("department", "desc") == ["a", "a", "b", "c"]
    assert await letters("left_warehouse_date", "desc") == ["c", "b", "a", "a"]
    assert await letters("left_warehouse_date", "asc") == ["b", "c", "a", "a"]
    assert await letters("warehouse_receiver", "asc") == ["c", "a", "a", "b"]  # Alice, bob, NULL
    # Lead time: a = 3 days, b = 10, c = 20 (no placed/approval → PO created_at).
    assert await letters("lead_time_days", "asc") == ["a", "a", "b", "c"]
    assert await letters("lead_time_days", "desc") == ["c", "b", "a", "a"]
    days = {it["description"]: it["lead_time_days"] for it in body["items"]}
    assert (days["a item 0"], days["b item 0"], days["c item 0"]) == (3, 10, 20)
    assert await letters("arrival_date", "desc") == ["a", "a", "c", "b"]
    assert await letters("date_ordered", "asc") == ["c", "b", "a", "a"]
    assert await letters("gr_number", "desc") == ["c", "b", "a", "a"]
    assert (await admin_client.get(url, params={"sort": "nope"})).status_code == 422
    assert (await admin_client.get(url + "/export", params={"sort": "nope"})).status_code == 422
    q_desc = await order("quantity", "desc")
    p1 = await _ids(admin_client, url, search=tok, sort="quantity", dir="desc", page=1, page_size=2)
    p2 = await _ids(admin_client, url, search=tok, sort="quantity", dir="desc", page=2, page_size=2)
    assert [it["description"] for it in p1["items"] + p2["items"]] == q_desc
    assert p1["total"] == 4


# ════════════════════════════════════════════════════════════════════════════
# Agreement receipts (cross-agreement list)
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_agreement_receipt_columns(admin_client, test_engine):
    tok = _tok()
    async with _factory(test_engine)() as db:
        who = await _user(db, "Rcpt Creator")
        zed, amy = await _user(db, "zed Picker"), await _user(db, "Amy Picker")
        vendor = await _vendor(db)
        agr = PurchaseAgreement(number=f"AGR-{tok}", title="agr", agreement_type="house_account",
                                vendor_id=vendor.id, vendor_name=vendor.name,
                                valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
                                status="active", created_by=who.id)
        db.add(agr)
        await db.flush()
        rows = [
            ("a", date(2026, 5, 1), "service", "ref-10", "Zeta", None, zed, "open"),
            ("b", date(2026, 3, 1), "counter_slip", "REF-9", "acme", "12.50", amy, "rejected"),
            ("c", date(2026, 4, 1), "delivery", None, None, "100", amy, "pending_ap_review"),
        ]
        ids = {}
        for i, (lbl, d, typ, ref, vn, total, rb, st) in enumerate(rows):
            r = AgreementReceipt(agreement_id=agr.id, receipt_type=typ, receipt_date=d,
                                 receipt_ref=ref, vendor_name=vn,
                                 total_amount=Decimal(total) if total else None,
                                 received_by=rb.id, status=st, created_by=who.id,
                                 created_at=_t(i))
            db.add(r)
            await db.flush()
            ids[lbl] = str(r.id)
        await db.commit()
        agr_id = str(agr.id)
    url = "/api/v1/agreement-receipts"
    rev = {v: k for k, v in ids.items()}

    async def order(key, d):
        body = await _ids(admin_client, url, agreement_id=agr_id, sort=key, dir=d)
        return [rev[i] for i in _order(body)]

    assert await order("receipt_date", "asc") == ["b", "c", "a"]
    assert await order("receipt_type", "asc") == ["b", "c", "a"]
    assert await order("receipt_ref", "asc") == ["a", "b", "c"]       # number-ish text, NULL last
    assert await order("receipt_ref", "desc") == ["b", "a", "c"]
    assert await order("vendor_name", "asc") == ["b", "a", "c"]
    assert await order("total_amount", "asc") == ["b", "c", "a"]      # 12.5 < 100, NULL last
    assert await order("total_amount", "desc") == ["c", "b", "a"]
    assert (await order("received_by_name", "asc"))[2] == "a"        # Amy, Amy, zed
    assert (await order("received_by_name", "desc"))[0] == "a"
    assert await order("status", "asc") == ["a", "c", "b"]
    assert len(await order("invoice_ref", "asc")) == 3                # all NULL → tiebreak only
    assert len(await order("attachment_count", "desc")) == 3          # all 0 → tiebreak only
    assert (await admin_client.get(url, params={"sort": "nope"})).status_code == 422
    body = await _ids(admin_client, url, agreement_id=agr_id)
    assert _order(body) == [ids["a"], ids["c"], ids["b"]]             # default receipt_date desc
    await _check_paging(admin_client, url, [ids["c"], ids["b"], ids["a"]],
                        agreement_id=agr_id, sort="total_amount", dir="desc")
