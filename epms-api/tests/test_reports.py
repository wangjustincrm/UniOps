"""Report export endpoint tests."""
import csv
import io
import pytest

REP_URL = "/api/v1/reports"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"
GR_URL = "/api/v1/gr"
INV_URL = "/api/v1/invoices"
PA_URL = "/api/v1/pa"

_PO_LINE = {"description": "Pump", "qty": "3", "unit": "EA", "unit_price": "200.00"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Rep Vendor", "category": "Parts",
        "contact_name": "R", "contact_email": "r@r.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_approved_po(client, vendor_id):
    po = await client.post(PO_URL, json={
        "title": "Rep PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": [_PO_LINE],
    })
    po.raise_for_status()
    po_id = po.json()["id"]
    for act in ["submit", "approve", "approve"]:
        (await client.post(f"{PO_URL}/{po_id}/action", json={"action": act})).raise_for_status()
    return po.json()


# ── Unauthenticated ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reports_require_auth(client):
    for path in ["pr", "po", "gr", "invoices", "pa", "budget", "vendors"]:
        r = await client.get(f"{REP_URL}/{path}")
        assert r.status_code == 403, f"{path} should require auth"


# ── PR report ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pr_report_csv(admin_client):
    r = await admin_client.get(f"{REP_URL}/pr")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "pr-report" in r.headers["content-disposition"]
    rows = _parse_csv(r.text)
    # Headers present
    if rows:
        assert "number" in rows[0]
        assert "status" in rows[0]
        assert "amount" in rows[0]


@pytest.mark.asyncio
async def test_pr_report_status_filter(admin_client):
    """Filter by status=draft returns only draft PRs (or empty)."""
    r = await admin_client.get(f"{REP_URL}/pr?status=draft")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["status"] == "draft"


@pytest.mark.asyncio
async def test_pr_report_date_filter(admin_client):
    r = await admin_client.get(f"{REP_URL}/pr?date_from=2026-01-01&date_to=2026-12-31")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]


# ── PO report ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_po_report_csv(admin_client):
    v = await _make_vendor(admin_client, "VND-REP-PO-01")
    await _make_approved_po(admin_client, v["id"])

    r = await admin_client.get(f"{REP_URL}/po")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    rows = _parse_csv(r.text)
    assert len(rows) >= 1
    assert "number" in rows[0]
    assert "total" in rows[0]
    assert "vendor_name" in rows[0]


@pytest.mark.asyncio
async def test_po_report_status_filter(admin_client):
    r = await admin_client.get(f"{REP_URL}/po?status=approved")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["status"] == "approved"


@pytest.mark.asyncio
async def test_po_report_vendor_filter(admin_client):
    v = await _make_vendor(admin_client, "VND-REP-PO-02")
    await _make_approved_po(admin_client, v["id"])

    r = await admin_client.get(f"{REP_URL}/po?vendor_id={v['id']}")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    assert len(rows) >= 1
    for row in rows:
        assert row["vendor_name"] == "Rep Vendor"


# ── GR report ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gr_report_csv(admin_client):
    r = await admin_client.get(f"{REP_URL}/gr")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    rows = _parse_csv(r.text)
    if rows:
        assert "number" in rows[0]
        assert "gr_type" in rows[0]
        assert "status" in rows[0]


@pytest.mark.asyncio
async def test_gr_report_type_filter(admin_client):
    r = await admin_client.get(f"{REP_URL}/gr?gr_type=physical")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["gr_type"] == "physical"


# ── Invoice report ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invoice_report_csv(admin_client):
    v = await _make_vendor(admin_client, "VND-REP-INV-01")
    inv_r = await admin_client.post(INV_URL, json={
        "vendor_id": v["id"],
        "vendor_invoice_number": "INV-REP-001",
        "amount": "600.00", "tax_amount": "78.00",
        "currency": "CAD",
        "invoice_date": "2026-03-01",
        "due_date": "2026-03-31",
    })
    assert inv_r.status_code == 201

    r = await admin_client.get(f"{REP_URL}/invoices")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    assert len(rows) >= 1
    assert "internal_ref" in rows[0]
    assert "total_amount" in rows[0]
    assert "status" in rows[0]


@pytest.mark.asyncio
async def test_invoice_report_status_filter(admin_client):
    r = await admin_client.get(f"{REP_URL}/invoices?status=unmatched")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["status"] == "unmatched"


# ── PA report ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pa_report_csv(admin_client):
    v = await _make_vendor(admin_client, "VND-REP-PA-01")
    po = await _make_approved_po(admin_client, v["id"])
    await admin_client.post(PA_URL, json={
        "po_id": po["id"], "title": "Rep PA",
        "pa_type": "regular", "subtotal": "600.00",
        "tax_amount": "78.00", "currency": "CAD",
        "line_items": [{"description": "Pump", "qty": "3", "unit": "EA", "unit_price": "200.00"}],
    })

    r = await admin_client.get(f"{REP_URL}/pa")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    assert len(rows) >= 1
    assert "pa_number" in rows[0]
    assert "payment_amount" in rows[0]
    assert "pa_type" in rows[0]


@pytest.mark.asyncio
async def test_pa_report_type_filter(admin_client):
    r = await admin_client.get(f"{REP_URL}/pa?pa_type=regular")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["pa_type"] == "regular"


# ── Budget report ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_report_csv(admin_client):
    r = await admin_client.get(f"{REP_URL}/budget")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    rows = _parse_csv(r.text)
    # May be empty if no budget data in test DB, but headers must be valid CSV
    if rows:
        assert "l1_code" in rows[0]
        assert "annual_budget" in rows[0]
        assert "utilisation_pct" in rows[0]


# ── Vendor report ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vendor_report_csv(admin_client):
    await _make_vendor(admin_client, "VND-REP-VND-01")

    r = await admin_client.get(f"{REP_URL}/vendors")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    assert len(rows) >= 1
    assert "code" in rows[0]
    assert "name" in rows[0]
    assert "category" in rows[0]
    assert "is_active" in rows[0]


@pytest.mark.asyncio
async def test_vendor_report_category_filter(admin_client):
    r = await admin_client.get(f"{REP_URL}/vendors?category=Parts")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["category"] == "Parts"


@pytest.mark.asyncio
async def test_vendor_report_active_only(admin_client):
    r = await admin_client.get(f"{REP_URL}/vendors?active_only=true")
    assert r.status_code == 200
    rows = _parse_csv(r.text)
    for row in rows:
        assert row["is_active"] == "True"


# ── Content-Disposition filename check ──────────────────────────────────────

@pytest.mark.asyncio
async def test_csv_filenames_contain_date(admin_client):
    from datetime import date
    today = str(date.today())
    for path in ["pr", "po", "gr", "invoices", "pa", "budget", "vendors"]:
        r = await admin_client.get(f"{REP_URL}/{path}")
        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert today in cd, f"{path} filename should contain today's date"
