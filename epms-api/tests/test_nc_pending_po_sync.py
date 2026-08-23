"""Mirroring NC purchase orders that are still IN APPROVAL (``forderstatus=2``).

Buyers need the UniOps PO PDF *before* NC approves the order — the printed PDF
is what gets signed off-line. So the sync now carries pending orders too, under
a status of their own (``nc_pending``) that no payment / receiving / matching
allow-list contains.

Three things this file pins, each of which was measured against production NC
(1,681 approved orders / 28 pending / 17 free-state) before it was written:

1. **Scope.** Pending orders come in, EXCEPT where the same ``vbillcode``
   already has an approved sibling — NC reuses document numbers, and the mirror
   would otherwise show ``PO-022-2512-01`` and ``PO-022-2512-01-2`` side by side
   with no way for a human to tell which one is real. Arrivals stay restricted
   to approved orders: a pending order has no goods receipt, by definition.

2. **The watermark.** ``PO_ORDER.modifiedtime`` is NULL on 27 of the 28 pending
   orders — and on 1,534 of the 1,681 approved ones. An incremental run keyed on
   it alone sees neither a newly submitted order nor, far worse, the 2→3
   approval that should flip a mirrored PO out of ``nc_pending``. ``taudittime``
   (set on every approved order) and ``creationtime`` are the fallbacks.

3. **Withdrawal.** An order rejected in NC drops back to ``forderstatus=0`` or
   is soft-deleted — either way it silently leaves the incremental result set,
   so nothing would ever update its mirror row. The reconcile pass cancels the
   mirrored pendings NC no longer lists.
"""
import re
import uuid
from decimal import Decimal

import pytest

from app.services.nc_purchase_sync import reader, writer
from app.services.nc_purchase_sync.transform import transform

CUT = "2024-01-01 00:00:00"
WM = "2026-08-01 00:00:00"


# ── fake Oracle ───────────────────────────────────────────────────────────────

class _FakeCursor:
    """Records every statement and answers it with canned rows.

    Rows are keyed by a substring of the SQL so a test can make one specific
    query return data without the fake having to interpret SQL — which it
    cannot do, and pretending otherwise would test the fake.
    """

    def __init__(self, canned=None):
        self.statements: list[str] = []
        self.description = []
        #: {sql fragment: (column names, rows)}
        self._canned = canned or {}
        self._rows = []

    def execute(self, sql, binds=None):
        self.statements.append(sql)
        low = sql.lower()
        for fragment, (cols, rows) in self._canned.items():
            if fragment.lower() in low:
                self.description = [(c,) for c in cols]
                self._rows = rows
                return
        if "bd_supplier" in low or "bd_measdoc" in low or "bd_currtype" in low:
            self.description = [("A",), ("B",)]
        elif "bd_material" in low:
            self.description = [("PK_MATERIAL",), ("CODE",), ("NAME",), ("ENAME",)]
        elif "po_order_b" in low:
            self.description = [("PK_ORDER_B",), ("PK_ORDER",)]
        elif "po_arriveorder" in low:
            self.description = [("PK_ARRIVEORDER",), ("MODIFIEDTIME",)]
        else:
            self.description = [("PK_ORDER",), ("CHANGED_AT",)]
        self._rows = []

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


def _fake(monkeypatch, canned=None):
    cur = _FakeCursor(canned)
    monkeypatch.setattr(reader, "_connect", lambda: _FakeConnection(cur))
    return cur


def _po_order_reads(cur) -> list[str]:
    """Statements reading PO_ORDER as the driving table (never PO_ORDER_B)."""
    return [s for s in cur.statements
            if re.search(r"\bNCSC\.PO_ORDER\b(?!_B)", s, re.IGNORECASE)]


def _driving_order_reads(cur) -> list[str]:
    """PO_ORDER reads that are NOT the arrival join — i.e. the ones that decide
    which orders exist for the mirror."""
    return [s for s in _po_order_reads(cur)
            if not re.search(r"po_arriveorder", s, re.IGNORECASE)]


# ── 1. scope ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("watermark", [None, WM], ids=["full", "incremental"])
def test_pending_orders_are_in_scope(monkeypatch, watermark):
    cur = _fake(monkeypatch)
    reader.fetch_nc(CUT, watermark)

    reads = _driving_order_reads(cur)
    assert reads, "expected the run to read PO_ORDER"
    assert any(re.search(r"forderstatus\s*=\s*2", s) for s in reads), (
        "no query admits in-approval orders:\n" + "\n---\n".join(reads))


@pytest.mark.parametrize("watermark", [None, WM], ids=["full", "incremental"])
def test_pending_scope_excludes_numbers_that_already_have_an_approved_sibling(
        monkeypatch, watermark):
    cur = _fake(monkeypatch)
    reader.fetch_nc(CUT, watermark)

    guarded = [s for s in _driving_order_reads(cur)
               if re.search(r"forderstatus\s*=\s*2", s)]
    assert guarded
    for sql in guarded:
        assert re.search(r"not\s+exists", sql, re.IGNORECASE), sql
        assert re.search(r"vbillcode\s*=\s*o\.vbillcode", sql, re.IGNORECASE), sql


@pytest.mark.parametrize("watermark", [None, WM], ids=["full", "incremental"])
def test_every_po_order_read_still_excludes_superseded_versions(monkeypatch, watermark):
    """The version guard predates this feature and must survive it."""
    cur = _fake(monkeypatch)
    reader.fetch_nc(CUT, watermark)

    reads = _po_order_reads(cur)
    assert reads
    assert all(re.search(r"bislatest\s*=\s*'Y'", s, re.IGNORECASE) for s in reads), (
        "\n---\n".join(s for s in reads
                       if not re.search(r"bislatest\s*=\s*'Y'", s, re.IGNORECASE)))


def test_arrivals_stay_restricted_to_approved_orders(monkeypatch):
    """A pending order has no arrival; admitting one would mirror a GR against a
    PO that does not legally exist yet."""
    cur = _fake(monkeypatch, {})
    reader.fetch_nc(CUT, WM)

    arrival_joins = [s for s in _po_order_reads(cur)
                     if re.search(r"po_arriveorder", s, re.IGNORECASE)]
    assert arrival_joins
    for sql in arrival_joins:
        assert re.search(r"o\.forderstatus\s*=\s*3", sql), sql
        assert not re.search(r"forderstatus\s*=\s*2", sql), sql


# ── 2. watermark ─────────────────────────────────────────────────────────────

def test_incremental_filter_falls_back_past_a_null_modifiedtime(monkeypatch):
    """NC leaves ``modifiedtime`` NULL on almost every order, and does not set it
    when an order is approved. Filtering on it alone makes the 2→3 flip
    invisible to every incremental run."""
    cur = _fake(monkeypatch)
    reader.fetch_nc(CUT, WM)

    driving = [s for s in _driving_order_reads(cur) if ":wm" in s]
    assert driving, "expected an incremental order query bound to the watermark"
    for sql in driving:
        assert re.search(r"taudittime", sql, re.IGNORECASE), sql
        assert re.search(r"creationtime", sql, re.IGNORECASE), sql
        assert not re.search(r"o\.modifiedtime\s*>=\s*:wm", sql, re.IGNORECASE), (
            "the bare modifiedtime comparison is the bug:\n" + sql)


def test_incremental_pk_selection_uses_the_coalesced_change_time():
    """``select_incremental_order_pks`` sees whatever the query aliased, so an
    order whose only timestamp is its approval time must still qualify."""
    approved_now = [{"pk_order": "O-APPROVED", "changed_at": "2026-08-20 10:00:00"}]
    stale = [{"pk_order": "O-OLD", "changed_at": "2026-07-01 09:00:00"}]

    pks = reader.select_incremental_order_pks(approved_now + stale, [], WM)

    assert pks == {"O-APPROVED"}


def test_incremental_pk_selection_still_reads_a_plain_modifiedtime():
    """Back-compat: rows carrying only the old key must not silently drop out."""
    rows = [{"pk_order": "O1", "modifiedtime": "2026-08-20 10:00:00"}]

    assert reader.select_incremental_order_pks(rows, [], WM) == {"O1"}


#: Matches only the header SELECT — ntotalorigmny appears in no other statement.
_HEADER_QUERY = "ntotalorigmny"
_HEADER_COLS = ("PK_ORDER", "MODIFIEDTIME", "TAUDITTIME", "CREATIONTIME")


def test_watermark_advances_on_the_approval_time_when_modifiedtime_is_null(monkeypatch):
    """Otherwise the watermark never moves and every "incremental" run re-reads
    the entire order book — 1,681 orders and 6,000 arrival lines, every time."""
    cur = _fake(monkeypatch, {_HEADER_QUERY: (
        _HEADER_COLS,
        [("O1", None, "2026-08-19 08:00:00", "2026-08-18 07:00:00")])})

    raw = reader.fetch_nc(CUT, None)

    assert cur.statements
    assert raw["max_modifiedtime"] == "2026-08-19 08:00:00"


def test_watermark_prefers_modifiedtime_when_nc_did_set_it(monkeypatch):
    _fake(monkeypatch, {_HEADER_QUERY: (
        _HEADER_COLS,
        [("O1", "2026-08-21 12:00:00", "2026-08-19 08:00:00",
          "2026-08-18 07:00:00")])})

    raw = reader.fetch_nc(CUT, None)

    assert raw["max_modifiedtime"] == "2026-08-21 12:00:00"


def test_watermark_falls_all_the_way_back_to_creation_time(monkeypatch):
    """A freshly submitted order has neither a modified nor an audit time."""
    _fake(monkeypatch, {_HEADER_QUERY: (
        _HEADER_COLS, [("O1", None, None, "2026-08-18 07:00:00")])})

    raw = reader.fetch_nc(CUT, None)

    assert raw["max_modifiedtime"] == "2026-08-18 07:00:00"


# ── 3. the pending set the reconcile pass needs ──────────────────────────────

@pytest.mark.parametrize("watermark", [None, WM], ids=["full", "incremental"])
def test_reader_reports_every_in_scope_order_pk(monkeypatch, watermark):
    """Unfiltered by the watermark on purpose: the reconcile pass has to tell
    "NC no longer lists this order" from "NC did not change it this run"."""
    cur = _fake(monkeypatch)
    raw = reader.fetch_nc(CUT, watermark)

    assert "in_scope_pks" in raw
    assert isinstance(raw["in_scope_pks"], (set, frozenset))
    scope_reads = [s for s in _driving_order_reads(cur) if ":wm" not in s]
    assert scope_reads, "the in-scope query must not be watermark-filtered"


# ── transform ────────────────────────────────────────────────────────────────

def _raw(order_overrides=None, line_overrides=None):
    order = {
        "pk_order": "O1", "vbillcode": "PO-001-2608-01",
        "dbilldate": "2026-09-01 00:00:00", "pk_supplier": "S1",
        "corigcurrencyid": "C1", "ntotalorigmny": Decimal("100"),
        "forderstatus": 3, "modifiedtime": None, "taudittime": None,
        "creationtime": "2026-08-10 21:38:29", "vmemo": None,
        "bfinalclose": None, "dclosedate": None,
        "vtrantypecode": "21-Cxx-CRM01",
    }
    order.update(order_overrides or {})
    line = {
        "pk_order_b": "OL1", "pk_order": "O1", "crowno": "1",
        "pk_material": "M1", "vvendinventoryname": "Widget",
        "castunitid": "U1", "nastnum": Decimal("10"),
        "norigtaxprice": Decimal("10"), "nqtorigtaxprice": Decimal("10"),
        "ntaxrate": Decimal("0"), "ctaxcodeid": None,
        "norigtaxmny": Decimal("100"), "norigmny": Decimal("100"),
        "ntax": Decimal("0"), "dplanarrvdate": "2026-09-15 00:00:00",
        "bpayclose": None, "binvoiceclose": None,
    }
    line.update(line_overrides or {})
    return {
        "orders": [order], "order_lines": [line],
        "arrivals": [], "arrival_lines": [], "invoiced_arrivals": set(),
        "suppliers": {"S1": "0000415"}, "materials": {"M1": ("MAT-1", "Widget")},
        "uoms": {"U1": "KGM"}, "currencies": {"C1": "CAD"},
        "max_modifiedtime": None, "in_scope_pks": {"O1"},
    }


_VENDORS = {"0000415": (uuid.uuid4(), "NC Vendor 415")}


def test_pending_order_is_mirrored_as_nc_pending():
    out = transform(_raw({"forderstatus": 2}), _VENDORS)

    assert [o["status"] for o in out["orders"]] == ["nc_pending"]
    assert "NC Pending Approval" in out["orders"][0]["notes"]


def test_approved_order_is_unaffected():
    out = transform(_raw(), _VENDORS)

    assert out["orders"][0]["status"] == "issued"
    assert "Pending" not in (out["orders"][0]["notes"] or "")


def test_pending_wins_over_the_milk_status():
    """A milk-type order still in approval is not payable *and* not final —
    ``nc_pending`` is the more restrictive fact, and it flips to ``nc_milk`` of
    its own accord once NC approves it."""
    out = transform(
        _raw({"forderstatus": 2, "vtrantypecode": "21-Cxx-MILK"}), _VENDORS)

    assert out["orders"][0]["status"] == "nc_pending"
    assert "Milk" in out["orders"][0]["notes"]


def test_pending_order_still_carries_its_lines():
    """The PDF is the whole point — an order with no lines prints nothing."""
    out = transform(_raw({"forderstatus": 2}), _VENDORS)

    assert len(out["order_lines"]) == 1
    assert out["order_lines"][0]["qty"] == Decimal("10")


def test_oracle_hands_the_status_back_as_a_decimal():
    """``oracledb.defaults.fetch_decimals`` is on, so NUMBER columns arrive as
    Decimal. A naive ``is 2`` / string compare would silently mirror every
    pending order as issued — i.e. as payable."""
    out = transform(_raw({"forderstatus": Decimal("2")}), _VENDORS)

    assert out["orders"][0]["status"] == "nc_pending"


# ── reconcile (real DB) ──────────────────────────────────────────────────────

def _insert_nc_po(cur, seeded_vendor, system_user_id, *, nc_pk, number, status):
    vid, vname = seeded_vendor
    pid = uuid.uuid4()
    cur.execute(
        "insert into purchase_orders (id,number,title,type,status,currency,subtotal,"
        "tax_rate,tax_amount,total,vendor_id,vendor_name,is_prepaid,approval_step_idx,"
        "pr_id,created_by,place_order_method,place_order_reference,source,nc_source_pk,"
        "notes,created_at,updated_at) values (%s,%s,%s,1,%s,'CAD',0,0,0,0,%s,%s,false,0,"
        "NULL,%s,'nc',%s,'nc',%s,%s,now(),now())",
        (pid, number, number, status, vid, vname, system_user_id, number, nc_pk,
         "memo"))
    return pid


def _status(cur, pid):
    cur.execute("select status, notes from purchase_orders where id=%s", (pid,))
    return cur.fetchone()


def test_reconcile_cancels_a_pending_po_nc_no_longer_lists(
        pg_cur, seeded_vendor, system_user_id):
    withdrawn = _insert_nc_po(pg_cur, seeded_vendor, system_user_id,
                              nc_pk="O-GONE", number="PO-GONE-01",
                              status="nc_pending")

    writer.reconcile_pending(pg_cur, {"O-STILL-THERE"})

    status, notes = _status(pg_cur, withdrawn)
    assert status == "cancelled"
    assert "NC Pending Withdrawn" in notes


def test_reconcile_leaves_a_pending_po_nc_still_lists(
        pg_cur, seeded_vendor, system_user_id):
    live = _insert_nc_po(pg_cur, seeded_vendor, system_user_id,
                         nc_pk="O-LIVE", number="PO-LIVE-01", status="nc_pending")

    writer.reconcile_pending(pg_cur, {"O-LIVE"})

    assert _status(pg_cur, live)[0] == "nc_pending"


def test_reconcile_leaves_an_order_that_got_approved(
        pg_cur, seeded_vendor, system_user_id):
    """2→3 keeps the pk in scope, so the upsert — not the reconcile — is what
    moves it to ``issued``. Cancelling it here would race the upsert and, when
    the upsert did not reach it, leave a live order dead in the mirror."""
    approved = _insert_nc_po(pg_cur, seeded_vendor, system_user_id,
                             nc_pk="O-APPROVED", number="PO-APPROVED-01",
                             status="nc_pending")

    writer.reconcile_pending(pg_cur, {"O-APPROVED"})

    assert _status(pg_cur, approved)[0] == "nc_pending"


@pytest.mark.parametrize("status", ["issued", "closed", "nc_milk", "cancelled"])
def test_reconcile_touches_nothing_but_pending_rows(
        pg_cur, seeded_vendor, system_user_id, status):
    other = _insert_nc_po(pg_cur, seeded_vendor, system_user_id,
                          nc_pk="O-OTHER", number=f"PO-OTHER-{status}",
                          status=status)

    writer.reconcile_pending(pg_cur, set())

    assert _status(pg_cur, other)[0] == status


def test_reconcile_is_idempotent(pg_cur, seeded_vendor, system_user_id):
    pid = _insert_nc_po(pg_cur, seeded_vendor, system_user_id,
                        nc_pk="O-GONE", number="PO-GONE-02", status="nc_pending")

    writer.reconcile_pending(pg_cur, set())
    first = _status(pg_cur, pid)[1]
    pg_cur.execute("update purchase_orders set status='nc_pending' where id=%s", (pid,))
    writer.reconcile_pending(pg_cur, set())

    assert _status(pg_cur, pid)[1] == first, "the marker must not be appended twice"


# ── allow-lists: pending must never become payable ───────────────────────────

def test_pending_is_not_invoice_matchable():
    from app.api.v1.invoices import _MATCHABLE_PO_STATUSES

    assert "nc_pending" not in _MATCHABLE_PO_STATUSES


def test_pending_can_produce_a_pdf():
    from app.api.v1.po_attachments import _PDF_STATUSES

    assert "nc_pending" in _PDF_STATUSES


def test_pending_cannot_receive_goods():
    """Both GR gates are literal tuples, so this reads them out of the source —
    the point is that adding a status must not quietly widen either one."""
    import inspect

    from app.api.v1 import gr as gr_api
    from app.crud import gr as gr_crud

    for mod in (gr_api, gr_crud):
        src = inspect.getsource(mod)
        for line in src.splitlines():
            if "status not in (" in line and "nc_pending" in line:
                pytest.fail(f"{mod.__name__}: pending PO admitted to receiving:\n{line}")
