"""Reading GHS label data off a supplier's safety data sheet.

Follows the pattern expense-api already uses for invoice OCR — the Anthropic
SDK, a document block, and a JSON envelope carrying a confidence per field —
because this plant already runs that and a second, different way of talking to
the same API would be one more thing to keep working.

Two departures from that service, both because of what this output is for.

**A bigger model.** Invoice OCR uses Haiku because it runs on every invoice and
a wrong vendor name is caught by the person paying it. This runs a few hundred
times ever, and its output goes onto a drum. A wrong pictogram is a WHMIS
violation that somebody will act on, so it gets the most capable model.

**Nothing here publishes.** The function returns a suggestion and its own
account of how sure it is. HSE's answer to question 6.8 was "HSE approval
required before any label can be printed", and the approval gate lives in the
label record, not in a confidence threshold. This module deliberately has no
concept of "confident enough to skip review".

When no API key is configured the extraction is simply unavailable and says so.
The label subsystem works without it — the draft is then filled from the
chemical record and edited by hand, which is what happens today on paper.
"""
from __future__ import annotations

import base64
import json
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# Low confidence is surfaced to the reviewer, not acted on. It exists so the
# person approving knows which fields to look at hardest — not so the system
# can decide anything.
REVIEW_CLOSELY_BELOW = 0.8

_PROMPT = """\
You are reading a safety data sheet to prepare a WHMIS 2015 workplace label.

Extract ONLY what the sheet actually states. Where the sheet does not say,
return null — that is the correct answer and is not a failure. Do not infer a
pictogram from a hazard statement, and do not complete a partial H-code.

Return a JSON object with EXACTLY this structure and no markdown:
{
  "product_identifier": {"value": "string or null", "confidence": 0.0-1.0},
  "supplier": {"value": "string or null", "confidence": 0.0-1.0},
  "signal_word": {"value": "Danger" or "Warning" or null, "confidence": 0.0-1.0},
  "pictograms": {"value": ["EXPLOSIVE"|"FLAMMABLE"|"OXIDIZING"|"COMPRESSED_GAS"|
                           "CORROSIVE"|"TOXIC"|"HARMFUL"|"HEALTH_HAZARD"|
                           "ENVIRONMENT"], "confidence": 0.0-1.0},
  "hazard_statements": {"value": [{"code": "H314", "text": "..."}],
                        "confidence": 0.0-1.0},
  "precautionary_statements": {"value": [{"code": "P280", "text": "..."}],
                               "confidence": 0.0-1.0},
  "first_aid": {"value": "string or null", "confidence": 0.0-1.0},
  "ppe_note": {"value": "string or null", "confidence": 0.0-1.0}
}

Rules:
- Section 2 of the sheet carries the label elements. Prefer it over any other.
- Give the H- and P-codes exactly as printed, with their own wording.
- first_aid: one or two sentences from Section 4, for the label.
- ppe_note: the personal protection from Section 8, briefly.
- Confidence reflects how plainly the sheet states it, not how likely you think
  it is to be true.
"""


class ExtractionUnavailable(RuntimeError):
    """No API key configured. The label is filled in by hand instead."""


def is_available() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _flatten(raw: dict) -> tuple[dict, dict, list[str]]:
    """Split the {value, confidence} envelope into values, confidences, and
    the list of fields a reviewer should look at hardest."""
    values: dict = {}
    confidence: dict = {}
    check: list[str] = []
    for field, envelope in (raw or {}).items():
        if not isinstance(envelope, dict):
            values[field] = envelope
            continue
        values[field] = envelope.get("value")
        score = envelope.get("confidence")
        if isinstance(score, (int, float)):
            confidence[field] = round(float(score), 2)
            if values[field] is not None and score < REVIEW_CLOSELY_BELOW:
                check.append(field)
    return values, confidence, check


async def extract_from_sds(file_bytes: bytes, mime_type: str) -> dict:
    """Read the label elements off a data sheet.

    Returns a suggestion. It is never applied without a person approving it —
    see the label record's status gate.
    """
    if not is_available():
        raise ExtractionUnavailable(
            "Reading data sheets automatically is not configured on this server. "
            "Fill the label in from the sheet by hand — it is a transcription "
            "either way, and nothing prints without approval.")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    if mime_type == "application/pdf":
        block = {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    else:
        block = {"type": "image",
                 "source": {"type": "base64", "media_type": mime_type, "data": b64}}

    try:
        response = client.messages.create(
            # The most capable model, unlike the invoice OCR next door: this
            # output goes onto a drum, and a wrong pictogram is a violation
            # somebody acts on rather than a typo somebody catches.
            model="claude-opus-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": [block, {"type": "text", "text": _PROMPT}]}],
        )
    except anthropic.BadRequestError as exc:
        logger.warning("SDS extraction could not read the file: %s", exc)
        raise ValueError(
            "That file could not be read. Fill the label in from the sheet by hand."
        ) from exc
    except anthropic.APIStatusError as exc:
        logger.error("Claude API error %s: %s", exc.status_code, exc.message)
        raise RuntimeError(f"Extraction service error: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        logger.error("Claude API unreachable: %s", exc)
        raise RuntimeError("Extraction service unreachable.") from exc

    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    try:
        raw = json.loads(text.strip().removeprefix("```json").removeprefix("```")
                         .removesuffix("```").strip())
    except json.JSONDecodeError:
        logger.error("SDS extraction returned something that was not JSON: %.200s", text)
        raise ValueError(
            "The data sheet could not be read into label fields. Fill it in by hand."
        ) from None

    values, confidence, check = _flatten(raw)
    return {
        "values": values,
        "confidence": confidence,
        # Named for what the reviewer should do with it. Nothing downstream
        # branches on this — it is not a threshold, it is a pointer.
        "check_closely": check,
        "model": response.model,
    }
