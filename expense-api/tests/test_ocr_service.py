"""Unit tests for ocr_service.extract_invoice — mocks the Anthropic client, no network.

Regression tests for the 2026-07 EPMS "AI parsing failed" bug: invoices with many
line items (e.g. 46-line lab-testing invoices) exceeded max_tokens=2048, Claude's
JSON was truncated mid-array, and json.loads failed → 422 to the user.
"""
import json
from types import SimpleNamespace

import pytest

from app.services import ocr_service


def _invoice_json(n_lines: int) -> dict:
    field = lambda v: {"value": v, "confidence": 1.0}  # noqa: E731
    return {
        "vendor_name": field("CCIC Agri-Food Testing (North America) Inc."),
        "invoice_number": field("2026000062"),
        "po_number": field(None),
        "invoice_date": field("2026-07-02"),
        "due_date": field(None),
        "payment_terms_net_days": field(30),
        "currency": field("CAD"),
        "subtotal": field(5666.38),
        "tax_amount": field(736.63),
        "total_amount": field(6403.01),
        "line_items": [
            {
                "description": f"Test item {i}",
                "quantity": 1,
                "unit_price": 10.0 + i,
                "amount": 10.0 + i,
                "tax_amount": 0,
            }
            for i in range(n_lines)
        ],
    }


class _FakeMessages:
    def __init__(self, holder):
        self._holder = holder

    def create(self, **kwargs):
        self._holder.request_kwargs = kwargs
        return self._holder.response


class _FakeClient:
    def __init__(self, holder):
        self.messages = _FakeMessages(holder)


class _Holder:
    request_kwargs: dict | None = None
    response = None


@pytest.fixture
def anthropic_stub(monkeypatch):
    """Patch anthropic.Anthropic with a stub; test sets .response, reads .request_kwargs."""
    import anthropic

    holder = _Holder()
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: _FakeClient(holder))
    monkeypatch.setattr(ocr_service.settings, "anthropic_api_key", "test-key")
    return holder


def _response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], stop_reason=stop_reason)


async def test_extract_invoice_keeps_all_46_line_items(anthropic_stub):
    """A 46-line invoice (real case: CCIC IVN#2026000062) must not be capped at 20."""
    anthropic_stub.response = _response(json.dumps(_invoice_json(46)))

    result = await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    assert len(result["line_items"]) == 46
    assert result["vendor_name"] == "CCIC Agri-Food Testing (North America) Inc."


async def test_extract_invoice_requests_enough_output_tokens(anthropic_stub):
    """max_tokens=2048 truncated many-line invoices; the request must allow >= 8192."""
    anthropic_stub.response = _response(json.dumps(_invoice_json(2)))

    await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    assert anthropic_stub.request_kwargs["max_tokens"] >= 8192


async def test_extract_invoice_truncated_output_raises_clear_error(anthropic_stub):
    """If the model still hits max_tokens, surface a truncation error, not 'invalid JSON'."""
    full = json.dumps(_invoice_json(46))
    anthropic_stub.response = _response(full[: len(full) // 2], stop_reason="max_tokens")

    with pytest.raises(ValueError, match="truncated"):
        await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")


# ── tax-inclusive line amount reconciliation ────────────────────────────────────
# Regression: 2026-07 Amazon Prime invoice. OCR returned unit_price 109.00 but the
# single line's `amount` = 123.17 (tax-inclusive). epms maps line `amount` ->
# line_total; the PO-match panel balances Σ line_total against the pre-tax header
# amount, so 123.17 vs 109 was off by exactly the tax and Match stayed disabled.

def _amazon_json() -> dict:
    field = lambda v: {"value": v, "confidence": 1.0}  # noqa: E731
    return {
        "vendor_name": field("Amazon"),
        "invoice_number": field("ACCU-INV-CA-2026-101278366"),
        "po_number": field(None),
        "invoice_date": field("2026-06-17"),
        "due_date": field("2026-07-17"),
        "payment_terms_net_days": field(None),
        "currency": field("CAD"),
        "subtotal": field(109.00),
        "tax_amount": field(14.17),
        "total_amount": field(123.17),
        "line_items": [
            {  # amount is tax-INCLUSIVE (the bug); unit_price is pre-tax
                "description": "Prime Business Annual Membership Fee - Essentials",
                "quantity": 1,
                "unit_price": 109.00,
                "amount": 123.17,
                "tax_amount": 0,
            }
        ],
    }


async def test_tax_inclusive_line_amount_is_corrected_to_pretax(anthropic_stub):
    """A single line whose amount carries tax is rewritten to the pre-tax subtotal."""
    anthropic_stub.response = _response(json.dumps(_amazon_json()))

    result = await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    line = result["line_items"][0]
    assert line["amount"] == 109.00          # was 123.17
    assert line["unit_price"] == 109.00      # untouched
    # header stays authoritative for tax
    assert result["subtotal"] == 109.00
    assert result["tax_amount"] == 14.17


def test_reconcile_fixes_tax_inclusive_single_line():
    lines = [{"quantity": 1, "unit_price": 109.00, "amount": 123.17, "tax_amount": 0}]
    out = ocr_service._reconcile_line_amounts(lines, 109.00, 14.17, 123.17)
    assert out[0]["amount"] == 109.00


def test_reconcile_fixes_tax_inclusive_multi_line():
    """Multi-line invoice where every line amount carries tax → all rewritten pre-tax."""
    lines = [
        {"quantity": 2, "unit_price": 50.0, "amount": 113.0, "tax_amount": 0},  # 100 pre-tax
        {"quantity": 1, "unit_price": 100.0, "amount": 113.0, "tax_amount": 0},  # 100 pre-tax
    ]
    out = ocr_service._reconcile_line_amounts(lines, 200.0, 26.0, 226.0)
    assert [li["amount"] for li in out] == [100.0, 100.0]


def test_reconcile_noop_when_lines_already_pretax():
    """Healthy invoice (lines sum to subtotal) is left untouched."""
    lines = [{"quantity": 1, "unit_price": 109.00, "amount": 109.00, "tax_amount": 0}]
    out = ocr_service._reconcile_line_amounts(lines, 109.00, 14.17, 123.17)
    assert out[0]["amount"] == 109.00


def test_reconcile_noop_when_no_tax():
    """No tax → pre-tax and tax-inclusive coincide; never touch amounts."""
    lines = [{"quantity": 1, "unit_price": 50.0, "amount": 50.0, "tax_amount": 0}]
    out = ocr_service._reconcile_line_amounts(lines, 50.0, 0, 50.0)
    assert out[0]["amount"] == 50.0


def test_reconcile_noop_when_unit_prices_also_tax_inclusive():
    """If unit_price × qty matches the total (not the subtotal), we cannot trust it as
    a pre-tax signal → leave amounts alone rather than mis-correct."""
    lines = [{"quantity": 1, "unit_price": 123.17, "amount": 123.17, "tax_amount": 0}]
    out = ocr_service._reconcile_line_amounts(lines, 109.00, 14.17, 123.17)
    assert out[0]["amount"] == 123.17  # unchanged — signal ambiguous

def test_reconcile_noop_when_header_totals_missing():
    lines = [{"quantity": 1, "unit_price": 109.00, "amount": 123.17, "tax_amount": 0}]
    out = ocr_service._reconcile_line_amounts(lines, None, 14.17, None)
    assert out[0]["amount"] == 123.17


# ── negative quantity normalization ─────────────────────────────────────────────
# Regression: 2026-08 Linde cylinder-rent invoice 58209691. Cylinder-return rows
# carry quantity -1; epms schemas keep the system-wide convention "negative lines
# are expressed via negative unit_price, quantity stays >= 0" (see epms po.py),
# so InvoiceLineItem.quantity ge=0 rejected the upload with a 422. OCR output is
# normalized here instead: qty := |qty|, unit_price := -unit_price (line amount
# is the product, so it is left untouched).

def test_negative_quantity_flipped_to_negative_unit_price():
    lines = [{"quantity": -1, "unit_price": 0.41, "amount": -0.41, "tax_amount": 0}]
    out = ocr_service._normalize_negative_quantities(lines)
    assert out[0]["quantity"] == 1
    assert out[0]["unit_price"] == -0.41
    assert out[0]["amount"] == -0.41  # product unchanged


def test_negative_quantity_with_zero_price_does_not_produce_negative_zero():
    """Linde balance rows: qty -1, price 0, amount 0 → qty 1, price stays 0 (not -0.0)."""
    lines = [{"quantity": -1, "unit_price": 0.0, "amount": 0.0, "tax_amount": 0}]
    out = ocr_service._normalize_negative_quantities(lines)
    assert out[0]["quantity"] == 1
    assert str(out[0]["unit_price"]) == "0.0"  # not "-0.0"


def test_positive_quantities_left_untouched():
    lines = [
        {"quantity": 9, "unit_price": 0.41, "amount": 3.69, "tax_amount": 0},
        {"quantity": 0, "unit_price": 5.0, "amount": 0.0, "tax_amount": 0},
    ]
    out = ocr_service._normalize_negative_quantities(lines)
    assert [li["quantity"] for li in out] == [9, 0]
    assert [li["unit_price"] for li in out] == [0.41, 5.0]


def _linde_json() -> dict:
    field = lambda v: {"value": v, "confidence": 1.0}  # noqa: E731
    return {
        "vendor_name": field("Linde Canada Inc"),
        "invoice_number": field("58209691"),
        "po_number": field("PO-113-2606-08"),
        "invoice_date": field("2026-07-31"),
        "due_date": field("2026-08-30"),
        "payment_terms_net_days": field(None),
        "currency": field("CAD"),
        "subtotal": field(1309.84),
        "tax_amount": field(170.28),
        "total_amount": field(1480.12),
        "line_items": [
            {"description": "T IND CYLINDER RENT", "quantity": -1, "unit_price": 0.0,
             "amount": 0.0, "tax_amount": 0},
            {"description": "M IND CYLINDER RENT", "quantity": 9, "unit_price": 0.41,
             "amount": 3.69, "tax_amount": 0},
            {"description": "R IND CYLINDER RENT", "quantity": -1, "unit_price": 0.0,
             "amount": 0.0, "tax_amount": 0},
        ],
    }


async def test_extract_invoice_normalizes_negative_quantities(anthropic_stub):
    """End-to-end: extracted lines never leave OCR with a negative quantity."""
    anthropic_stub.response = _response(json.dumps(_linde_json()))

    result = await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    quantities = [li["quantity"] for li in result["line_items"]]
    assert quantities == [1, 9, 1]
    assert all(q >= 0 for q in quantities)
    # positive row untouched
    assert result["line_items"][1]["unit_price"] == 0.41
