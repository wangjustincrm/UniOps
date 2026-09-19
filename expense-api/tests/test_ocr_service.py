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


def test_document_type_is_extracted_when_present():
    from app.services import ocr_service
    parsed = {
        "vendor_name": {"value": "Amazon Business", "confidence": 1.0},
        "document_type": {"value": "credit_note", "confidence": 0.95},
        "subtotal": {"value": -0.04, "confidence": 1.0},
        "line_items": [],
    }
    result = ocr_service._assemble_invoice_result(parsed)
    assert result["document_type"] == "credit_note"


def test_document_type_defaults_to_invoice_when_absent():
    from app.services import ocr_service
    result = ocr_service._assemble_invoice_result(
        {"vendor_name": {"value": "ULINE", "confidence": 1.0}, "line_items": []})
    assert result["document_type"] == "invoice"


def test_document_type_falls_back_on_an_unexpected_value():
    from app.services import ocr_service
    result = ocr_service._assemble_invoice_result(
        {"document_type": {"value": "receipt", "confidence": 0.4}, "line_items": []})
    assert result["document_type"] == "invoice"


# ── 400 triage: account limit vs unreadable document ───────────────────────────
# Regression: 2026-09-08. The Anthropic account hit its usage limit; the API
# answered 400 "You have reached your specified API usage limits". That is a
# BadRequestError, same class as "this PDF cannot be decoded", so every EPMS
# upload told the user "AI parsing failed — please fill fields manually" and the
# perfectly good invoice (Abell Pest Control A8239014) took the blame. The two
# must not read alike: an account limit is an outage nobody at a desk can fix.

def _bad_request(message: str):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        message,
        response=httpx.Response(400, request=request),
        body={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
    )


class _RaisingMessages:
    def __init__(self, exc):
        self._exc = exc

    def create(self, **kwargs):
        raise self._exc


@pytest.fixture
def anthropic_raises(monkeypatch):
    """Patch anthropic.Anthropic so .messages.create raises the exception given."""
    import anthropic

    def _install(exc):
        client = SimpleNamespace(messages=_RaisingMessages(exc))
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: client)
        monkeypatch.setattr(ocr_service.settings, "anthropic_api_key", "test-key")

    return _install


_USAGE_LIMIT = (
    "You have reached your specified API usage limits. "
    "You will regain access on 2026-10-01 at 00:00 UTC."
)


async def test_usage_limit_is_an_outage_not_an_unreadable_file(anthropic_raises):
    """RuntimeError -> HTTP 503, and the wording must clear the file of blame."""
    anthropic_raises(_bad_request(_USAGE_LIMIT))

    with pytest.raises(RuntimeError) as excinfo:
        await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    text = str(excinfo.value)
    assert "NOT a problem with your file" in text
    assert "usage limit" in text.lower()          # the provider's own reason is carried through
    assert "manually" in text.lower()             # ...and the user still knows what to do next


async def test_low_credit_balance_is_also_an_outage(anthropic_raises):
    anthropic_raises(_bad_request("Your credit balance is too low to access the Anthropic API"))

    with pytest.raises(RuntimeError):
        await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")


async def test_unreadable_document_still_sends_the_user_to_manual_entry(anthropic_raises):
    """The pre-existing behaviour for a genuine file problem must not change."""
    anthropic_raises(_bad_request("Could not process image: unsupported or corrupt file"))

    with pytest.raises(ValueError, match="Could not read this document"):
        await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")


async def test_receipt_mode_triages_the_same_way(anthropic_raises):
    anthropic_raises(_bad_request(_USAGE_LIMIT))

    with pytest.raises(RuntimeError, match="NOT a problem with your file"):
        await ocr_service.extract_receipt(b"%PDF-fake", "application/pdf")


async def test_slip_mode_triages_the_same_way(anthropic_raises):
    anthropic_raises(_bad_request(_USAGE_LIMIT))

    with pytest.raises(RuntimeError, match="NOT a problem with your file"):
        await ocr_service.extract_slip(b"%PDF-fake", "application/pdf")


async def test_slip_unreadable_document_keeps_the_manual_entry_message(anthropic_raises):
    anthropic_raises(_bad_request("Could not process image: unsupported or corrupt file"))

    with pytest.raises(ValueError, match="Could not read this document"):
        await ocr_service.extract_slip(b"%PDF-fake", "application/pdf")


# ── total check: does amount + tax add up to the total printed on the invoice? ──
# Regression: 2026-09 EPMS uploads where the parsed Amount was short of the
# document. Two shapes, both silent — EPMS derives total_amount = amount +
# tax_amount, so anything left out of those two fields is money the vendor never
# gets paid, and the invoice still matches, approves and pays through cleanly.
#   (a) an invoice mixing taxed and untaxed lines: the model sums only the taxed
#       rows into "subtotal" and the untaxed line vanishes from the header;
#   (b) a footer charge (freight, late fee, handling) printed under the subtotal:
#       neither a line nor a tax, so it had nowhere to go and was dropped.

def _totals(subtotal, tax, total, lines, charges=None, label=None) -> tuple[dict, list[dict], dict]:
    """Run _reconcile_totals over a header/lines pair; return (result, lines, check)."""
    result = {"subtotal": subtotal, "tax_amount": tax, "total_amount": total,
              "other_charges": charges, "other_charges_label": label}
    rows = [
        {"line_number": i + 1, "description": d, "quantity": 1.0,
         "unit_price": a, "amount": a, "tax_amount": t}
        for i, (d, a, t) in enumerate(lines)
    ]
    check = ocr_service._reconcile_totals(result, rows)
    return result, rows, check


def test_untaxed_line_left_out_of_subtotal_is_recovered():
    """(a) Taxed lines 1000 + untaxed line 200; the model's subtotal counts only
    the taxed 1000, but the printed total 1330 proves the line list is right."""
    result, _, check = _totals(
        subtotal=1000.00, tax=130.00, total=1330.00,
        lines=[("Taxable goods", 1000.00, 130.00), ("Exempt service", 200.00, 0.0)],
    )
    assert result["subtotal"] == 1200.00          # was 1000.00 — the untaxed line is back
    assert check["status"] == "repaired"
    assert check["difference"] == 0.0
    assert check["line_sum"] == 1200.00
    assert any("line items" in r for r in check["repairs"])


def test_freight_printed_under_the_subtotal_is_added_to_the_amount():
    """(b) Subtotal 1000 + freight 75 + tax 139.75 = 1214.75 printed. Freight has
    no column of its own in EPMS, so it is folded into the pre-tax amount and
    itemised — otherwise the vendor is paid 75 short."""
    result, rows, check = _totals(
        subtotal=1000.00, tax=139.75, total=1214.75,
        lines=[("Widgets", 1000.00, 130.00)],
        charges=75.00, label="Freight",
    )
    assert result["subtotal"] == 1075.00
    assert check["status"] == "repaired"
    assert check["difference"] == 0.0
    assert rows[-1]["description"] == "Freight"
    assert rows[-1]["amount"] == 75.00
    assert rows[-1]["tax_amount"] == 0.0


def test_late_fee_without_a_label_still_lands_as_a_line():
    result, rows, _ = _totals(
        subtotal=500.00, tax=65.00, total=590.00,
        lines=[("Service", 500.00, 65.00)],
        charges=25.00,
    )
    assert result["subtotal"] == 525.00
    assert rows[-1]["description"] == "Other charges"


def test_charge_already_inside_the_subtotal_is_not_counted_twice():
    """Model reports freight both as a charge AND inside the subtotal. The
    arithmetic (subtotal + tax already == total) says so; do not fold."""
    result, rows, check = _totals(
        subtotal=1075.00, tax=139.75, total=1214.75,
        lines=[("Widgets", 1000.00, 130.00), ("Freight", 75.00, 9.75)],
        charges=75.00, label="Freight",
    )
    assert result["subtotal"] == 1075.00
    assert len(rows) == 2                         # nothing appended
    assert check["status"] == "ok"


def test_unexplained_difference_is_reported_not_guessed():
    """A residual could be a misread tax as easily as a missing line — moving it
    into the amount would fix the payable and corrupt the tax. Report it."""
    result, rows, check = _totals(
        subtotal=1000.00, tax=130.00, total=1230.00,
        lines=[("Widgets", 1000.00, 130.00)],
    )
    assert result["subtotal"] == 1000.00          # untouched
    assert len(rows) == 1
    assert check["status"] == "mismatch"
    assert check["difference"] == 100.00
    assert check["document_total"] == 1230.00
    assert check["computed_total"] == 1130.00


def test_healthy_invoice_reports_ok_and_changes_nothing():
    result, rows, check = _totals(
        subtotal=1000.00, tax=130.00, total=1130.00,
        lines=[("Widgets", 600.00, 78.00), ("Gadgets", 400.00, 52.00)],
    )
    assert result["subtotal"] == 1000.00
    assert len(rows) == 2
    assert check["status"] == "ok"
    assert check["repairs"] == []
    assert check["notes"] == []


def test_penny_rounding_is_not_a_mismatch():
    """Vendors round; 1 cent of drift must not block an upload."""
    _, _, check = _totals(
        subtotal=1000.00, tax=130.00, total=1130.01,
        lines=[("Widgets", 1000.00, 130.00)],
    )
    assert check["status"] == "ok"


def test_float_noise_is_not_a_mismatch():
    """109.00 + 14.17 is 123.17000000000002 in binary float — cents, not floats."""
    _, _, check = _totals(
        subtotal=109.00, tax=14.17, total=123.17,
        lines=[("Prime membership", 109.00, 0.0)],
    )
    assert check["status"] == "ok"
    assert check["difference"] == 0.0


def test_no_printed_total_is_unverified_not_ok():
    """Nothing to check against — say so rather than claim the figures agree."""
    _, _, check = _totals(
        subtotal=1000.00, tax=130.00, total=None,
        lines=[("Widgets", 1000.00, 130.00)],
    )
    assert check["status"] == "unverified"
    assert check["difference"] is None
    assert check["document_total"] is None


def test_charge_without_a_printed_total_is_flagged_not_guessed_at():
    """With no grand total, "billed on top of the subtotal" and "already one of
    the lines" look identical — both leave the line sum equal to the subtotal.
    Folding on a coin flip would double-pay half the time; say so instead."""
    result, rows, check = _totals(
        subtotal=1000.00, tax=130.00, total=None,
        lines=[("Widgets", 1000.00, 130.00)],
        charges=75.00, label="Freight",
    )
    assert result["subtotal"] == 1000.00
    assert len(rows) == 1
    assert check["status"] == "unverified"
    assert check["repairs"] == []
    assert any("Freight" in n and "75.00" in n for n in check["notes"])


def test_missing_subtotal_is_taken_from_the_lines():
    result, _, check = _totals(
        subtotal=None, tax=130.00, total=1130.00,
        lines=[("Widgets", 600.00, 78.00), ("Gadgets", 400.00, 52.00)],
    )
    assert result["subtotal"] == 1000.00
    assert check["status"] == "repaired"
    assert check["difference"] == 0.0


def test_missing_subtotal_and_no_lines_falls_back_to_the_total():
    result, _, check = _totals(
        subtotal=None, tax=130.00, total=1130.00, lines=[],
    )
    assert result["subtotal"] == 1000.00
    assert check["line_sum"] is None
    assert check["status"] == "repaired"


def test_credit_note_negatives_reconcile_the_same_way():
    """Sign is carried through, not abs()'d — a credit note must check out too."""
    result, _, check = _totals(
        subtotal=-500.00, tax=-65.00, total=-565.00,
        lines=[("Returned goods", -500.00, -65.00)],
    )
    assert result["subtotal"] == -500.00
    assert check["status"] == "ok"


def _mixed_tax_json() -> dict:
    """Taxed + untaxed lines, subtotal counting only the taxed one (failure (a))."""
    field = lambda v: {"value": v, "confidence": 1.0}  # noqa: E731
    return {
        "document_type": field("invoice"),
        "vendor_name": field("Mixed Supply Co"),
        "invoice_number": field("MS-7781"),
        "po_number": field(None),
        "invoice_date": field("2026-09-02"),
        "due_date": field(None),
        "payment_terms_net_days": field(30),
        "currency": field("CAD"),
        "subtotal": field(1000.00),      # the untaxed 200 line is missing from it
        "tax_amount": field(130.00),
        "other_charges": field(None),
        "total_amount": field(1330.00),  # what the document prints
        "line_items": [
            {"description": "Taxable goods", "quantity": 1, "unit_price": 1000.00,
             "amount": 1000.00, "tax_amount": 130.00},
            {"description": "Exempt service", "quantity": 1, "unit_price": 200.00,
             "amount": 200.00, "tax_amount": 0},
        ],
    }


async def test_extract_invoice_recovers_the_untaxed_line(anthropic_stub):
    """End to end: the amount EPMS prefills equals the document, not the taxed part."""
    anthropic_stub.response = _response(json.dumps(_mixed_tax_json()))

    result = await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    assert result["subtotal"] == 1200.00
    assert result["subtotal"] + result["tax_amount"] == result["total_amount"]
    assert result["total_check"]["status"] == "repaired"


def _freight_json() -> dict:
    field = lambda v: {"value": v, "confidence": 1.0}  # noqa: E731
    doc = _mixed_tax_json()
    doc["subtotal"] = field(1000.00)
    doc["tax_amount"] = field(130.00)
    doc["other_charges"] = {"value": 75.00, "label": "Freight", "confidence": 1.0}
    doc["total_amount"] = field(1205.00)
    doc["line_items"] = [
        {"description": "Taxable goods", "quantity": 1, "unit_price": 1000.00,
         "amount": 1000.00, "tax_amount": 130.00},
    ]
    return doc


async def test_extract_invoice_folds_freight_into_the_amount(anthropic_stub):
    anthropic_stub.response = _response(json.dumps(_freight_json()))

    result = await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    assert result["subtotal"] == 1075.00
    assert result["line_items"][-1]["description"] == "Freight"
    assert result["total_check"]["status"] == "repaired"
    assert result["total_check"]["difference"] == 0.0


async def test_extract_invoice_flags_a_total_it_cannot_explain(anthropic_stub):
    doc = _mixed_tax_json()
    doc["line_items"] = doc["line_items"][:1]          # the 200 line never got extracted
    anthropic_stub.response = _response(json.dumps(doc))

    result = await ocr_service.extract_invoice(b"%PDF-fake", "application/pdf")

    assert result["subtotal"] == 1000.00               # nothing invented
    assert result["total_check"]["status"] == "mismatch"
    assert result["total_check"]["difference"] == 200.00


def test_prompt_states_the_two_rules_the_failures_broke():
    """The repairs above are a net, not the fix — the model has to be told."""
    assert "other_charges" in ocr_service._INVOICE_PROMPT
    assert "Never sum only the taxed lines." in ocr_service._INVOICE_PROMPT
