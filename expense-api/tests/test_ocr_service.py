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
