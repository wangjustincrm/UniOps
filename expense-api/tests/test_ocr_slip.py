"""Unit tests for ocr_service.extract_slip — mocks the Anthropic client, no network.

`slip` is the OCR mode for house_account pickup slips (agreement purchases):
a paper slip logged digitally, matched by date + amount, not by a reference
number (the ref is a nice-to-have, not a required field).
"""
import json
from types import SimpleNamespace

import pytest

from app.services import ocr_service


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


def _field(v):
    return {"value": v, "confidence": 1.0}


def _slip_json(
    slip_ref="TILL-04-8821",
    date="2026-08-05",
    amount=100.00,
    tax_amount=13.00,
    total_amount=113.00,
    currency="CAD",
) -> dict:
    return {
        "slip_ref": _field(slip_ref),
        "date": _field(date),
        "amount": _field(amount),
        "tax_amount": _field(tax_amount),
        "total_amount": _field(total_amount),
        "currency": _field(currency),
    }


async def test_extract_slip_returns_expected_fields(anthropic_stub):
    """返回 slip_ref / date / amount / tax_amount / total_amount / currency。"""
    anthropic_stub.response = _response(json.dumps(_slip_json()))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result == {
        "slip_ref": "TILL-04-8821",
        "date": "2026-08-05",
        "amount": 100.00,
        "tax_amount": 13.00,
        "total_amount": 113.00,
        "currency": "CAD",
    }


async def test_slip_ref_absent_returns_none_not_an_error(anthropic_stub):
    """凭证上没有可用编号是正常情况 —— 基线匹配不需要它。返回 None,不是抛异常。"""
    anthropic_stub.response = _response(json.dumps(_slip_json(slip_ref=None)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["slip_ref"] is None
    # the rest of the extraction still succeeds
    assert result["amount"] == 100.00
    assert result["total_amount"] == 113.00


async def test_unreadable_file_raises_value_error_not_runtime_error(monkeypatch):
    """anthropic.BadRequestError → ValueError → 调用方转 422 "手动录入", 不是 503。
    OCR 不可用时录入页必须照常工作。"""
    import anthropic
    import httpx

    def _raise(**kwargs):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(400, request=request)
        raise anthropic.BadRequestError(message="unsupported file", response=response, body=None)

    class _RaisingMessages:
        create = staticmethod(_raise)

    class _RaisingClient:
        def __init__(self, api_key):
            self.messages = _RaisingMessages()

    monkeypatch.setattr(anthropic, "Anthropic", _RaisingClient)
    monkeypatch.setattr(ocr_service.settings, "anthropic_api_key", "test-key")

    with pytest.raises(ValueError):
        await ocr_service.extract_slip(b"corrupt-bytes", "image/heic")


def test_receipt_prompt_is_unchanged():
    """OA 报销在生产使用 receipt 模式。提示词不是加法 —— 改一句可能扰动既有
    字段的抽取。用一个显式断言把它钉死,防止后来的人"顺手统一"两段提示词。"""
    from app.services.ocr_service import _RECEIPT_PROMPT

    assert "vendor_name" in _RECEIPT_PROMPT
    assert "slip_ref" not in _RECEIPT_PROMPT
