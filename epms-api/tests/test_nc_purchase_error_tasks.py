"""What the NC purchase sync could not import must land in the Admin Task Inbox.

Before this, a skipped order left nothing but an integer on the run row:
PO-029-2609-01 (and four August orders) went missing for days because their ERP
supplier had no UniOps vendor, and no screen said so. These tests pin the two
halves that failed then: the task appears with the REASON on it, and it closes
only on evidence — never merely because a later (windowed) run did not mention
the order again.
"""
import uuid
from decimal import Decimal

from app.services.nc_purchase_sync import error_tasks
from app.services.nc_purchase_sync.transform import transform


# ── the reason reaches the payload ───────────────────────────────────────────

def _raw_missing_vendor():
    """A reader-shaped payload whose single order has an unknown supplier."""
    return {
        "orders": [{"pk_order": "O1", "vbillcode": "PO-029-2609-01",
                    "pk_supplier": "S1", "corigcurrencyid": "C1",
                    "ntotalorigmny": Decimal("100"), "vmemo": None,
                    "vtrantypecode": "21-Cxx-CRM01",
                    "taudittime": "2026-09-04 22:46:46",
                    "creationtime": "2026-09-04 22:43:54"}],
        "order_lines": [], "arrivals": [], "arrival_lines": [],
        "suppliers": {"S1": "0001134"},
        "supplier_names": {"0001134": "Independent Chemical"},
        "materials": {}, "uoms": {}, "currencies": {"C1": "CAD"},
        "max_modifiedtime": None,
    }


def test_transform_reports_which_supplier_is_missing():
    r = transform(_raw_missing_vendor(), {})      # no vendors at all
    assert r["skipped_no_vendor"] == ["PO-029-2609-01"]   # unchanged contract
    gap = r["vendor_gaps"][0]
    assert gap["number"] == "PO-029-2609-01"
    assert gap["supplier_code"] == "0001134"
    assert gap["supplier_name"] == "Independent Chemical"
    # changed_at is what an admin has to hand the rewind script to re-open the
    # window on this order — greatest() over the NC change columns.
    assert gap["changed_at"] == "2026-09-04 22:46:46"


def test_transform_vendor_gaps_empty_when_everything_resolves():
    raw = _raw_missing_vendor()
    r = transform(raw, {"0001134": (uuid.uuid4(), "Independent Chemical")})
    assert r["vendor_gaps"] == [] and r["skipped_no_vendor"] == []


# ── the task ─────────────────────────────────────────────────────────────────

GAP = {"number": "PO-029-2609-01", "supplier_code": "0001134",
       "supplier_name": "Independent Chemical",
       "changed_at": "2026-09-04 22:46:46"}


def _record(cur, uid, **kw):
    kw.setdefault("vendor_gaps", [GAP])
    kw.setdefault("collision_numbers", [])
    kw.setdefault("in_scope_numbers", {"PO-029-2609-01"})
    return error_tasks.record_import_errors(cur, uuid.uuid4(), created_by=uid, **kw)


def _open_rows(cur, number="PO-029-2609-01"):
    cur.execute("select id, type, priority, assigned_role, assigned_user_id, title, "
                "description, vendor from tasks where document_type=%s "
                "and document_number=%s and is_completed is false",
                (error_tasks.DOC_TYPE, number))
    return cur.fetchall()


def test_vendor_gap_raises_an_admin_task_naming_the_supplier(pg_cur, system_user_id):
    counts = _record(pg_cur, system_user_id)
    assert counts["opened"] == 1
    rows = _open_rows(pg_cur)
    assert len(rows) == 1
    _id, ttype, priority, role, assignee, title, description, vendor = rows[0]
    assert ttype == error_tasks.VENDOR_TASK
    # Broadcast to every system_admin, not to one person.
    assert role == "system_admin" and assignee is None
    assert "PO-029-2609-01" in title and "0001134" in title
    assert "Independent Chemical" in title
    assert vendor == "Independent Chemical"
    # The reason, the fix, and why the sync will look stuck until it is done
    # (it holds its watermark at this order rather than leaving it behind).
    assert "Import from ERP" in description
    assert "holds its position" in description
    assert "2026-09-04 22:46:46" in description
    assert priority == "normal"


def test_repeat_runs_refresh_the_same_task_instead_of_piling_up(pg_cur, system_user_id):
    _record(pg_cur, system_user_id)
    second = _record(pg_cur, system_user_id)
    assert second["opened"] == 0
    assert len(_open_rows(pg_cur)) == 1


def test_task_stays_open_while_the_order_is_still_missing(pg_cur, system_user_id):
    """The closing rule must not be "this run didn't mention it". An incremental
    run reads a WINDOW: the order skipped last week is absent from today's
    payload while still missing, and closing on that absence retires the task
    with the problem unfixed."""
    _record(pg_cur, system_user_id)
    later = _record(pg_cur, system_user_id, vendor_gaps=[])   # not in this window
    assert later["closed"] == 0
    assert len(_open_rows(pg_cur)) == 1


def test_task_closes_once_the_order_reaches_epms(pg_cur, system_user_id, seeded_vendor):
    """Positive evidence: the mirror now holds the order. Written through the
    real writer so the test closes on what production actually inserts."""
    from app.services.nc_purchase_sync import writer
    _record(pg_cur, system_user_id)
    vid, vname = seeded_vendor
    writer.upsert(pg_cur, {
        "orders": [{
            "nc_source_pk": "O-029", "number": "PO-029-2609-01",
            "title": "PO-029-2609-01", "type": 1, "status": "issued",
            "source": "nc", "currency": "CAD", "total": Decimal("100.00"),
            "subtotal": Decimal("100.00"), "tax_rate": Decimal("0"),
            "tax_amount": Decimal("0"), "vendor_id": vid, "vendor_name": vname,
            "pr_id": None, "place_order_method": "nc",
            "place_order_reference": "PO-029-2609-01", "notes": None,
        }],
        "order_lines": [], "grs": [], "gr_lines": [], "skipped_no_vendor": [],
    }, system_user_id)
    out = _record(pg_cur, system_user_id, vendor_gaps=[])
    assert out["closed"] == 1
    assert _open_rows(pg_cur) == []


def test_task_closes_when_nc_stops_listing_the_order(pg_cur, system_user_id):
    """Rejected / deleted / superseded in NC: the order will never arrive, and
    the task must not outlive it."""
    _record(pg_cur, system_user_id)
    out = _record(pg_cur, system_user_id, vendor_gaps=[], in_scope_numbers=set())
    assert out["closed"] == 1
    assert _open_rows(pg_cur) == []


def test_unknown_scope_never_closes_anything(pg_cur, system_user_id):
    """`in_scope_numbers` is None when the reader reported no scope set (a
    stubbed fetch). An empty set would then read as "NC lists nothing" and
    retire every open task."""
    _record(pg_cur, system_user_id)
    out = _record(pg_cur, system_user_id, vendor_gaps=[], in_scope_numbers=None)
    assert out["closed"] == 0 and len(_open_rows(pg_cur)) == 1


def test_number_collision_raises_its_own_task(pg_cur, system_user_id):
    _record(pg_cur, system_user_id, vendor_gaps=[],
            collision_numbers=["PO-022-2512-01"],
            in_scope_numbers={"PO-022-2512-01"})
    rows = _open_rows(pg_cur, "PO-022-2512-01")
    assert len(rows) == 1 and rows[0][1] == error_tasks.FAILURE_TASK
    assert "no free document number" in rows[0][5]


# ── run-level failure ────────────────────────────────────────────────────────

def test_failed_run_raises_one_urgent_task_and_a_success_clears_it(pg_cur, system_user_id):
    error_tasks.record_run_failure(pg_cur, uuid.uuid4(), "ORA-12541: TNS:no listener",
                                   system_user_id)
    error_tasks.record_run_failure(pg_cur, uuid.uuid4(), "ORA-12541: TNS:no listener",
                                   system_user_id)
    rows = _open_rows(pg_cur, error_tasks.RUN_FAILURE_KEY)
    assert len(rows) == 1, "one task per broken hour would bury the inbox"
    assert rows[0][2] == "urgent"
    assert "ORA-12541" in rows[0][6]
    assert error_tasks.clear_run_failure(pg_cur) == 1
    assert _open_rows(pg_cur, error_tasks.RUN_FAILURE_KEY) == []


def test_import_error_pass_leaves_the_run_failure_task_alone(pg_cur, system_user_id):
    """Its closing condition is a successful run, not the state of one order."""
    error_tasks.record_run_failure(pg_cur, uuid.uuid4(), "boom", system_user_id)
    _record(pg_cur, system_user_id, vendor_gaps=[], in_scope_numbers=set())
    assert len(_open_rows(pg_cur, error_tasks.RUN_FAILURE_KEY)) == 1
