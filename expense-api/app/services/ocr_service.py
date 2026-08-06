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
  "document_type": {"value": "invoice" or "credit_note", "confidence": 0.0-1.0},
  "vendor_name": {"value": "string or null", "confidence": 0.0-1.0},
  "invoice_number": {"value": "string or null", "confidence": 0.0-1.0},
  "po_number": {"value": "string or null", "confidence": 0.0-1.0},
  "invoice_date": {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "due_date": {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "payment_terms_net_days": {"value": number or null, "confidence": 0.0-1.0},
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

Line item field meaning (READ CAREFULLY):
- "unit_price": the PRE-TAX price of one unit.
- "amount": the line's PRE-TAX extended price = quantity × unit_price, EXCLUDING tax.
- "tax_amount": the tax charged on this line only (0 if the invoice taxes at the footer).
- The sum of every line's "amount" MUST equal the header "subtotal" (pre-tax).
- NEVER put the tax-inclusive figure in a line's "amount". For a single-line invoice
  whose only printed number is the grand total, still report "amount" as the pre-tax
  line value (= subtotal), and carry the tax in the header "tax_amount".

Rules:
- Confidence 1.0 = clearly printed, unambiguous
- Confidence 0.5 = partially visible, estimated, or inferred
- Confidence 0.0 = field not found or unreadable
- Dates must be YYYY-MM-DD; null if not found
- Numbers must be plain decimals (no currency symbols)
- Default currency to CAD if not specified
- "due_date": extract ONLY if an explicit due/payment date is printed on the
  invoice. Do NOT derive or calculate it from payment terms — leave it null and let
  payment_terms_net_days carry the terms (the application computes the date).
- "po_number": a purchase order reference the vendor prints on the invoice, often
  starting with "PO-" (also "P.O.", "Order No", "Purchase Order"). Null if absent.
- "payment_terms_net_days": if the invoice shows payment terms like "NET 30",
  "Net 45", "Due in 60 days", extract only the integer number of days (e.g. 30).
  Null if no such terms are printed. Extract this even when an explicit due_date is
  also shown; do NOT compute due_date from the terms (the application does that).
- If there are no line items, return an empty array
- "line_items": include at most 100 rows; if the invoice has more, keep the first 100
- "document_type": "credit_note" when the document is a credit note / credit memo /
  credit invoice / adjustment note / avoir / 贷项通知单, or when the payable total is
  negative. Otherwise "invoice".
- Report amounts exactly as printed, including minus signs. Do NOT flip signs to
  make a credit note look like an invoice.
- Return ONLY the JSON object, minified (no indentation or extra whitespace), no other text"""

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


def _reconcile_line_amounts(
    lines: list[dict],
    subtotal: float | None,
    tax_amount: float | None,
    total_amount: float | None,
) -> list[dict]:
    """Repair line ``amount`` values that OCR filled with the tax-INCLUSIVE figure.

    Observed failure (2026-07, single-line Amazon Prime invoice): unit_price 109.00
    but line ``amount`` came back as 123.17 (= 109 + 13% tax). Downstream, epms maps
    line ``amount`` -> line_total and the PO-match panel balances Σ line_total against
    the invoice's PRE-TAX header amount, so a tax-inclusive line total is off by exactly
    the tax and the match can never balance (Match button stays disabled).

    Fires only when the evidence is unambiguous: the lines sum to the tax-inclusive
    total while unit_price × quantity sums to the pre-tax subtotal. Then each line's
    ``amount`` is rewritten to its pre-tax extended price. No-op when any signal is
    missing, when there is no tax, or when the sums don't clearly indicate contamination
    (e.g. unit prices are themselves tax-inclusive) — never "corrects" a healthy invoice.
    """
    if not lines or subtotal is None or total_amount is None or not tax_amount:
        return lines

    def _close(a: float, b: float) -> bool:
        return abs(a - b) <= max(0.02, abs(b) * 0.005)

    reported = sum(li["amount"] for li in lines)
    computed = sum(li["unit_price"] * li["quantity"] for li in lines)

    if _close(reported, total_amount) and not _close(reported, subtotal) and _close(computed, subtotal):
        for li in lines:
            li["amount"] = round(li["unit_price"] * li["quantity"], 2)
    return lines


def _normalize_negative_quantities(lines: list[dict]) -> list[dict]:
    """Rewrite negative-quantity rows as positive-quantity, negative-price rows.

    Observed failure (2026-08, Linde cylinder-rent invoice 58209691): cylinder-return
    rows carry quantity -1, but the system-wide convention is "negative lines are
    expressed via negative unit_price, quantity stays >= 0" (see epms po.py), and
    InvoiceLineItem.quantity enforces ge=0 — so the upload 422'd. Flipping the sign
    onto unit_price preserves the product (line amount is left untouched).
    """
    for li in lines:
        if li["quantity"] < 0:
            li["quantity"] = abs(li["quantity"])
            li["unit_price"] = -li["unit_price"] if li["unit_price"] else 0.0
    return lines


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


_VALID_DOCUMENT_TYPES = ("invoice", "credit_note")


def _assemble_invoice_result(parsed: dict) -> dict:
    """Flatten the model's {value, confidence} envelope into a flat result.

    Split out of extract_invoice so the mapping is unit-testable without an
    Anthropic API call.
    """
    scalar_fields = ["vendor_name", "invoice_number", "po_number",
                     "invoice_date", "due_date", "payment_terms_net_days",
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

    # Document type steers the EPMS upload form down the credit-note branch.
    # An unrecognised or missing value falls back to "invoice": the frontend
    # still catches a negative total, and mis-labelling a real invoice as a
    # credit would be the more damaging error.
    dt_item = parsed.get("document_type", {})
    dt = dt_item.get("value") if isinstance(dt_item, dict) else None
    result["document_type"] = dt if dt in _VALID_DOCUMENT_TYPES else "invoice"

    raw_lines = parsed.get("line_items", []) or []
    lines = []
    for i, li in enumerate(raw_lines[:100]):
        lines.append({
            "line_number": i + 1,
            "description": str(li.get("description", "")),
            "quantity": float(li.get("quantity", 1) or 1),
            "unit_price": float(li.get("unit_price", 0) or 0),
            "amount": float(li.get("amount", 0) or 0),
            "tax_amount": float(li.get("tax_amount", 0) or 0),
        })

    def _num(v: object) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    lines = _normalize_negative_quantities(lines)
    lines = _reconcile_line_amounts(
        lines, _num(result.get("subtotal")), _num(result.get("tax_amount")),
        _num(result.get("total_amount")),
    )

    result["line_items"] = lines
    result["ocr_confidence"] = (
        round(sum(confidences) / len(confidences), 4) if confidences else 0.0)
    result["low_confidence_fields"] = low_confidence
    result["ocr_raw"] = parsed
    return result


async def extract_invoice(file_bytes: bytes, mime_type: str) -> dict:
    """Run invoice OCR and return structured extraction with confidence scores.

    Returns:
        {
            vendor_name, invoice_number, po_number,
            invoice_date, due_date, payment_terms_net_days,
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
        # 8192 covers ~100 minified line items; 2048 truncated invoices with
        # many lines (46-line lab invoice → JSON cut mid-array, 2026-07).
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
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

    if response.stop_reason == "max_tokens":
        log.error("Invoice OCR output truncated at max_tokens — invoice likely has too many line items")
        raise ValueError("OCR output was truncated — the invoice may have too many line items")

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

    return _assemble_invoice_result(parsed)


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
