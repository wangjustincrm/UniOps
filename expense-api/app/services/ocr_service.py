"""Invoice OCR service using Claude API vision.

Supports two modes:
  invoice  — full extraction for PA-DIR (vendor, invoice_no, dates, line_items, totals)
  receipt  — simple extraction for EXP (vendor, date, total, tax, description)
"""
import base64
import json
import logging
from typing import Literal

from app.core.config import settings

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.75  # fields below this are flagged for user review

_INVOICE_PROMPT = """\
You are an invoice data extraction assistant. Extract all available information from this invoice image or PDF.

Return a JSON object with EXACTLY this structure (no extra keys, no markdown):
{
  "vendor_name": {"value": "string or null", "confidence": 0.0-1.0},
  "invoice_number": {"value": "string or null", "confidence": 0.0-1.0},
  "invoice_date": {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "due_date": {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "currency": {"value": "CAD", "confidence": 0.0-1.0},
  "subtotal": {"value": number or null, "confidence": 0.0-1.0},
  "tax_amount": {"value": number or null, "confidence": 0.0-1.0},
  "total_amount": {"value": number or null, "confidence": 0.0-1.0},
  "line_items": [
    {
      "description": "string",
      "quantity": number,
      "unit_price": number,
      "amount": number,
      "tax_amount": number
    }
  ]
}

Rules:
- Confidence 1.0 = clearly printed, unambiguous
- Confidence 0.5 = partially visible, estimated, or inferred
- Confidence 0.0 = field not found or unreadable
- Dates must be YYYY-MM-DD; null if not found
- Numbers must be plain decimals (no currency symbols)
- Default currency to CAD if not specified
- If there are no line items, return an empty array
- Return ONLY the JSON object, no other text"""

_RECEIPT_PROMPT = """\
Extract receipt information from this image.

Return ONLY this JSON (no markdown):
{
  "vendor_name": {"value": "string or null", "confidence": 0.0-1.0},
  "date": {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "description": {"value": "string or null", "confidence": 0.0-1.0},
  "total_amount": {"value": number or null, "confidence": 0.0-1.0},
  "tax_amount": {"value": number or null, "confidence": 0.0-1.0},
  "currency": {"value": "CAD", "confidence": 0.0-1.0}
}"""


def _mime_to_media_type(mime: str) -> str:
    mapping = {
        "image/jpeg": "image/jpeg",
        "image/jpg": "image/jpeg",
        "image/png": "image/png",
        "image/gif": "image/gif",
        "image/webp": "image/webp",
        "application/pdf": "application/pdf",
    }
    return mapping.get(mime.lower(), "image/jpeg")


async def extract_invoice(file_bytes: bytes, mime_type: str) -> dict:
    """Run invoice OCR and return structured extraction with confidence scores.

    Returns:
        {
            vendor_name, invoice_number, invoice_date, due_date,
            currency, subtotal, tax_amount, total_amount,
            line_items: [...],
            ocr_confidence: float,          # average confidence
            low_confidence_fields: [str],   # fields below threshold
        }

    Raises:
        RuntimeError: if Claude API is unavailable (HTTP 5xx / network)
        ValueError: if the response cannot be parsed
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured — OCR is unavailable")

    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    media_type = _mime_to_media_type(mime_type)

    # Build content block — PDF uses document type, images use image type
    if media_type == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [content_block, {"type": "text", "text": _INVOICE_PROMPT}],
            }],
        )
    except anthropic.BadRequestError as exc:
        log.warning("Invoice OCR could not process the file: %s", exc)
        raise ValueError("Could not read this document. Please enter the details manually.")
    except anthropic.APIStatusError as exc:
        log.error("Claude API error %d: %s", exc.status_code, exc.message)
        raise RuntimeError(f"OCR service error: {exc.message}")
    except anthropic.APIConnectionError as exc:
        log.error("Claude API connection error: %s", exc)
        raise RuntimeError("OCR service unreachable — check ANTHROPIC_API_KEY and network")

    raw_text = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.error("OCR JSON parse error: %s\nRaw: %s", exc, raw_text[:500])
        raise ValueError(f"OCR returned invalid JSON: {exc}")

    # Flatten extracted values and calculate confidence
    scalar_fields = ["vendor_name", "invoice_number", "invoice_date", "due_date",
                     "currency", "subtotal", "tax_amount", "total_amount"]

    result: dict = {}
    confidences: list[float] = []
    low_confidence: list[str] = []

    for field in scalar_fields:
        item = parsed.get(field, {})
        value = item.get("value") if isinstance(item, dict) else None
        conf = float(item.get("confidence", 0.0)) if isinstance(item, dict) else 0.0
        result[field] = value
        if value is not None:
            confidences.append(conf)
            if conf < CONFIDENCE_THRESHOLD:
                low_confidence.append(field)

    # Line items (no confidence per-item in the response, use overall confidence)
    raw_lines = parsed.get("line_items", []) or []
    lines = []
    for i, li in enumerate(raw_lines[:20]):  # cap at 20 lines
        lines.append({
            "line_number": i + 1,
            "description": str(li.get("description", "")),
            "quantity": float(li.get("quantity", 1) or 1),
            "unit_price": float(li.get("unit_price", 0) or 0),
            "amount": float(li.get("amount", 0) or 0),
            "tax_amount": float(li.get("tax_amount", 0) or 0),
        })

    overall_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    result["line_items"] = lines
    result["ocr_confidence"] = overall_confidence
    result["low_confidence_fields"] = low_confidence
    result["ocr_raw"] = parsed

    return result


async def extract_receipt(file_bytes: bytes, mime_type: str) -> dict:
    """Simple receipt OCR for EXP line item pre-fill."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    media_type = _mime_to_media_type(mime_type)

    # PDF receipts must be sent as a document block; images as an image block.
    if media_type == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": [content_block, {"type": "text", "text": _RECEIPT_PROMPT}]}],
        )
    except anthropic.BadRequestError as exc:
        # Unreadable / unsupported file (e.g. HEIC, corrupt) — degrade to manual entry
        # (caller maps ValueError → HTTP 422) rather than a service-outage 503.
        log.warning("Receipt OCR could not process the file: %s", exc)
        raise ValueError("Could not read this document. Please enter the details manually.")
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        raise RuntimeError(f"OCR service error: {exc}")

    raw_text = response.content[0].text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        raise ValueError("OCR returned invalid JSON")

    return {
        "vendor_name": parsed.get("vendor_name", {}).get("value"),
        "date": parsed.get("date", {}).get("value"),
        "description": parsed.get("description", {}).get("value"),
        "total_amount": parsed.get("total_amount", {}).get("value"),
        "tax_amount": parsed.get("tax_amount", {}).get("value"),
        "currency": parsed.get("currency", {}).get("value", "CAD"),
    }
