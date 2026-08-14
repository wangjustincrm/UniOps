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
    vendor_name="PRINCESS AUTO #12",
    slip_ref="TILL-04-8821",
    date="2026-08-05",
    amount=100.00,
    tax_amount=13.00,
    total_amount=113.00,
    currency="CAD",
) -> dict:
    return {
        "vendor_name": _field(vendor_name),
        "slip_ref": _field(slip_ref),
        "date": _field(date),
        "amount": _field(amount),
        "tax_amount": _field(tax_amount),
        "total_amount": _field(total_amount),
        "currency": _field(currency),
    }


async def test_extract_slip_returns_expected_fields(anthropic_stub):
    """返回 vendor_name / slip_ref / date / amount / tax_amount / total_amount / currency。"""
    anthropic_stub.response = _response(json.dumps(_slip_json()))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result == {
        "vendor_name": "PRINCESS AUTO #12",
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


# ── Task 13: the merchant printed on the slip ─────────────────────────────
# epms stores it and compares it against the AGREEMENT's vendor, because a
# slip from shop A recorded against shop B's house account is the classic
# house-account mis-posting and the agreement's own vendor_name can never
# reveal it.

async def test_extract_slip_returns_the_vendor_printed_on_the_slip(anthropic_stub):
    anthropic_stub.response = _response(json.dumps(_slip_json(
        vendor_name="Canadian Tire #241")))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["vendor_name"] == "Canadian Tire #241"


async def test_vendor_absent_returns_none_not_an_error(anthropic_stub):
    """抽不到商家名是正常情况(小票抬头模糊/被裁掉),录入人可以补。
    返回 None,其余字段照常抽出。"""
    anthropic_stub.response = _response(json.dumps(_slip_json(vendor_name=None)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["vendor_name"] is None
    # the rest of the extraction still succeeds
    assert result["slip_ref"] == "TILL-04-8821"
    assert result["total_amount"] == 113.00


async def test_vendor_key_missing_entirely_is_none_not_a_keyerror(anthropic_stub):
    """老模型/裁剪过的响应里可能根本没有这个键 —— 必须是 None,不是 KeyError,
    否则整次识别失败、录入人退回全手工。"""
    payload = _slip_json()
    del payload["vendor_name"]
    anthropic_stub.response = _response(json.dumps(payload))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["vendor_name"] is None
    assert result["amount"] == 100.00


# ── Review round 1 (Minor 2): the prompt invites nulls, so the response
# parser must survive them in BOTH shapes a model can produce — the pair with
# a null inside (covered above) and a bare null in place of the pair. The
# second used to be an AttributeError, i.e. a 500 on the whole extraction and
# the recorder thrown back to fully manual entry. ─────────────────────────

async def test_a_bare_null_field_does_not_kill_the_whole_extraction(anthropic_stub):
    payload = _slip_json()
    payload["vendor_name"] = None
    anthropic_stub.response = _response(json.dumps(payload))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["vendor_name"] is None
    # everything else still came through — one null field costs nothing
    assert result["slip_ref"] == "TILL-04-8821"
    assert result["amount"] == 100.00
    assert result["total_amount"] == 113.00


async def test_bare_nulls_across_every_slip_field_still_return_a_dict(anthropic_stub):
    """把每个字段都换成裸 null —— 一个都不许炸,全部落各自的默认值。
    裸 null 与"键缺失"是同一件事(都没有 {value, confidence} 这个对),
    所以 currency 落 "CAD" —— 而 {"value": null} 那种写法仍然落 None
    (见 test_vendor_absent_returns_none_not_an_error),与改动前一致。"""
    anthropic_stub.response = _response(json.dumps(
        {k: None for k in _slip_json()}))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result == {
        "vendor_name": None, "slip_ref": None, "date": None,
        "amount": None, "tax_amount": None, "total_amount": None,
        "currency": "CAD",
    }


async def test_an_explicit_null_value_pair_is_none_not_the_default(anthropic_stub):
    """{"value": null} 是"模型看了但没读到" —— 与"裸 null / 键缺失"分开处理,
    currency 在这种写法下仍然是 None,与改动前逐字一致。"""
    payload = _slip_json()
    payload["currency"] = {"value": None, "confidence": 0.1}
    anthropic_stub.response = _response(json.dumps(payload))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["currency"] is None


async def test_a_missing_currency_key_still_defaults_to_cad(anthropic_stub):
    """键缺失时的既有行为不许变 —— 这是 _slip_field 的 default 参数唯一的用处。"""
    payload = _slip_json()
    del payload["currency"]
    anthropic_stub.response = _response(json.dumps(payload))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["currency"] == "CAD"


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


def test_invoice_prompt_is_unchanged():
    """发票 OCR 在生产用 invoice 模式(PA-DIR)。Task 13 只加 _SLIP_PROMPT 的
    vendor_name,这段一个字都不许动 —— 同样用显式断言钉死。"""
    from app.services.ocr_service import _INVOICE_PROMPT

    assert "line_items" in _INVOICE_PROMPT
    assert "slip_ref" not in _INVOICE_PROMPT
    assert "printed at the top of the slip" not in _INVOICE_PROMPT


def test_receipt_prompt_is_unchanged():
    """OA 报销在生产使用 receipt 模式。提示词不是加法 —— 改一句可能扰动既有
    字段的抽取。用一个显式断言把它钉死,防止后来的人"顺手统一"两段提示词。"""
    from app.services.ocr_service import _RECEIPT_PROMPT

    assert "vendor_name" in _RECEIPT_PROMPT
    assert "slip_ref" not in _RECEIPT_PROMPT
    # Task 13 added a vendor_name bullet to _SLIP_PROMPT only; this pins that
    # its wording never got "helpfully unified" into the receipt prompt.
    assert "printed at the top of the slip" not in _RECEIPT_PROMPT


# ── Whole-branch review (small item A): counter slips routinely print only a
# subtotal and a total, and the prompt allows null for every field. The EPMS
# slip form requires all three amounts, so a null tax left the user stuck on
# "Amount, tax and total are all required" with no hint that the answer was
# total − amount. ────────────────────────────────────────────────────────

async def test_null_tax_is_derived_from_amount_and_total(anthropic_stub):
    anthropic_stub.response = _response(json.dumps(_slip_json(
        amount=100.00, tax_amount=None, total_amount=113.00)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["tax_amount"] == 13.00
    # Must survive the backend's exact-Decimal check on a Numeric(15,2)
    # column: float subtraction here yields 13.000000000000014.
    assert round(result["amount"] * 100) + round(result["tax_amount"] * 100) == round(
        result["total_amount"] * 100)


async def test_null_tax_on_a_zero_rated_slip_derives_zero_not_null(anthropic_stub):
    anthropic_stub.response = _response(json.dumps(_slip_json(
        amount=42.50, tax_amount=None, total_amount=42.50)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["tax_amount"] == 0


async def test_null_tax_stays_null_when_the_other_two_are_not_both_present(anthropic_stub):
    anthropic_stub.response = _response(json.dumps(_slip_json(
        amount=None, tax_amount=None, total_amount=113.00)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["tax_amount"] is None
    assert result["amount"] is None


async def test_null_tax_stays_null_when_total_is_below_the_subtotal(anthropic_stub):
    # A negative "tax" means OCR misread one of the two figures — prefilling
    # it would put a value in the form that cannot be right and that the
    # backend would reject anyway. Leave it for the recorder to key in.
    anthropic_stub.response = _response(json.dumps(_slip_json(
        amount=113.00, tax_amount=None, total_amount=100.00)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["tax_amount"] is None


async def test_an_explicit_zero_tax_is_not_overwritten(anthropic_stub):
    # 0 is falsy — the derivation must key off `is None`, not truthiness, or a
    # genuinely zero-rated slip whose OCR DID read the tax line would get
    # silently recomputed.
    anthropic_stub.response = _response(json.dumps(_slip_json(
        amount=100.00, tax_amount=0, total_amount=113.00)))

    result = await ocr_service.extract_slip(b"fake-image-bytes", "image/jpeg")

    assert result["tax_amount"] == 0
