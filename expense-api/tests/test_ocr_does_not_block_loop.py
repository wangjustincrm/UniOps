"""OCR must not hold the event loop while Claude is thinking.

`anthropic.Anthropic` is the synchronous client — `.messages.create()` blocks
the calling thread. extract_invoice / extract_receipt / extract_slip are
`async def` and are awaited straight from the /ocr/{mode} handler, so calling
it inline stalled the whole asyncio loop for the 5-20s a vision read of a
multi-page PDF takes. For that window expense-api answered nothing at all: not
the task list, not an approval, not an attachment download.

These tests pin the fix by measuring the loop, not by inspecting the code: a
ticker coroutine runs alongside the extraction and must keep getting scheduled.
Against the old inline call the ticker cannot advance past its first await.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.services import ocr_service

# Long enough that a blocked loop is unmistakable, short enough to stay a unit
# test. The ticker sleeps 1ms, so an unblocked loop gets ~100 turns in.
_API_LATENCY_S = 0.30
_TICK_S = 0.001


class _SlowMessages:
    """Stands in for the blocking SDK call — time.sleep, not asyncio.sleep."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        time.sleep(_API_LATENCY_S)
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(self._payload))],
            stop_reason="end_turn",
        )


@pytest.fixture
def slow_anthropic(monkeypatch):
    import anthropic

    field = lambda v: {"value": v, "confidence": 1.0}  # noqa: E731
    payload = {
        "vendor_name": field("Titan Power Ltd"),
        "invoice_number": field("INV-1"),
        "invoice_date": field("2026-09-10"),
        "currency": field("CAD"),
        "subtotal": field(1000.0),
        "tax_amount": field(130.0),
        "total_amount": field(1130.0),
        "line_items": [],
        # receipt / slip shapes read the same envelope
        "description": field("Taxi"),
        "date": field("2026-09-10"),
        "amount": field(1130.0),
        "slip_ref": field("SL-1"),
    }
    messages = _SlowMessages(payload)
    monkeypatch.setattr(
        anthropic, "Anthropic",
        lambda api_key: SimpleNamespace(messages=messages),
    )
    monkeypatch.setattr(ocr_service.settings, "anthropic_api_key", "test-key")
    return messages


async def _ticks_during(coro) -> int:
    """Run `coro`, counting how many times a parallel ticker gets scheduled."""
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(_TICK_S)
            ticks += 1

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0)          # let the ticker reach its first await
    try:
        await coro
    finally:
        stop = True
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    return ticks


@pytest.mark.parametrize("extract, args", [
    ("extract_invoice", (b"%PDF-fake", "application/pdf")),
    ("extract_receipt", (b"\xff\xd8fake", "image/jpeg")),
    ("extract_slip",    (b"\xff\xd8fake", "image/jpeg")),
])
async def test_extraction_leaves_the_loop_free(slow_anthropic, extract, args):
    fn = getattr(ocr_service, extract)

    ticks = await _ticks_during(fn(*args))

    assert slow_anthropic.call_count == 1
    # A free loop gets ~300 ticks in 0.30s; a blocked one gets 0-1. Assert well
    # clear of both so the test is not timing-flaky on a loaded box.
    assert ticks >= 20, f"event loop was stalled during {extract} (ticks={ticks})"


async def test_two_extractions_overlap(slow_anthropic):
    """Two concurrent scans should finish in about one API latency, not two."""
    started = asyncio.get_running_loop().time()

    await asyncio.gather(
        ocr_service.extract_invoice(b"%PDF-fake", "application/pdf"),
        ocr_service.extract_invoice(b"%PDF-fake", "application/pdf"),
    )

    elapsed = asyncio.get_running_loop().time() - started
    assert slow_anthropic.call_count == 2
    assert elapsed < _API_LATENCY_S * 1.8, (
        f"the two scans serialised ({elapsed:.2f}s for 2x{_API_LATENCY_S}s)"
    )
