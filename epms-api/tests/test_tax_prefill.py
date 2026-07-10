"""Tax-line auto-prefill — pure inference tests (no DB, no network)."""
import pytest
from decimal import Decimal

from app.services.tax_prefill import pick_tax_code

CODES = [
    {"code": "HST-ON", "rate": "0.13", "recoverable": True},
    {"code": "GST", "rate": "0.05", "recoverable": True},
    {"code": "PST-BC", "rate": "0.07", "recoverable": False},
]


def test_pick_exact_rate_match():
    # 4.81 / 36.99 = 13.0035% → HST-ON (±0.5pp)
    assert pick_tax_code(Decimal("36.99"), Decimal("4.81"), CODES)["code"] == "HST-ON"


def test_pick_no_match_returns_none():
    # 10% falls in no code's tolerance band
    assert pick_tax_code(Decimal("100"), Decimal("10"), CODES) is None


def test_pick_ambiguous_returns_none():
    # two codes within tolerance of 5.2% (GST 5% and a hypothetical 5.4%)
    codes = CODES + [{"code": "X-54", "rate": "0.054", "recoverable": True}]
    assert pick_tax_code(Decimal("100"), Decimal("5.2"), codes) is None


def test_pick_zero_tax_returns_none():
    assert pick_tax_code(Decimal("100"), Decimal("0"), CODES) is None
    assert pick_tax_code(Decimal("0"), Decimal("5"), CODES) is None
    assert pick_tax_code(Decimal("100"), Decimal("-1"), CODES) is None
    assert pick_tax_code(Decimal("-50"), Decimal("5"), CODES) is None


INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Tax Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


def _inv(vendor_id, amount, tax):
    return {
        "vendor_id": vendor_id, "vendor_invoice_number": f"TAXPRE-{amount}-{tax}",
        "amount": amount, "tax_amount": tax, "currency": "CAD",
        "invoice_date": "2026-07-09", "due_date": "2026-08-08",
    }


class _FakeMdm:
    """Stands in for MdmClient — returns canned tax codes."""
    def __init__(self, codes):
        self._codes = codes

    def __call__(self, bearer_token):  # constructor signature parity
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_tax_codes(self):
        if isinstance(self._codes, Exception):
            raise self._codes
        return self._codes


HST = [{"code": "HST-ON", "rate": "0.13", "recoverable": True},
       {"code": "GST", "rate": "0.05", "recoverable": True}]


@pytest.mark.asyncio
async def test_create_prefills_tax_line_on_unique_match(admin_client, monkeypatch):
    from app.services import tax_prefill
    monkeypatch.setattr(tax_prefill, "MdmClient", _FakeMdm(HST))
    v = await _make_vendor(admin_client, "VND-TAXPRE-01")
    inv = (await admin_client.post(INV_URL, json=_inv(v["id"], "100.00", "13.00"))).json()

    r = await admin_client.get(f"{INV_URL}/{inv['id']}/tax-lines")
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["tax_code"] == "HST-ON"
    assert lines[0]["taxable_amount"] == "100.00"
    assert lines[0]["tax_amount"] == "13.00"      # 保留 header 原值,不重算
    assert lines[0]["recoverable"] is True


@pytest.mark.asyncio
async def test_create_skips_prefill_when_no_rate_match(admin_client, monkeypatch):
    from app.services import tax_prefill
    monkeypatch.setattr(tax_prefill, "MdmClient", _FakeMdm(HST))
    v = await _make_vendor(admin_client, "VND-TAXPRE-02")
    inv = (await admin_client.post(INV_URL, json=_inv(v["id"], "100.00", "9.00"))).json()
    assert (await admin_client.get(f"{INV_URL}/{inv['id']}/tax-lines")).json()["lines"] == []


@pytest.mark.asyncio
async def test_create_survives_mdm_failure(admin_client, monkeypatch):
    from app.services import tax_prefill
    monkeypatch.setattr(tax_prefill, "MdmClient", _FakeMdm(RuntimeError("mdm down")))
    v = await _make_vendor(admin_client, "VND-TAXPRE-03")
    r = await admin_client.post(INV_URL, json=_inv(v["id"], "100.00", "13.00"))
    assert r.status_code == 201                    # fail-open:发票照常创建
    assert (await admin_client.get(f"{INV_URL}/{r.json()['id']}/tax-lines")).json()["lines"] == []
