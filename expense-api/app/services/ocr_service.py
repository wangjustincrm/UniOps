"""Invoice OCR service using Claude API vision.

Supports three modes:
  invoice  — full extraction for PA-DIR (vendor, invoice_no, dates, line_items, totals)
  receipt  — simple extraction for EXP (vendor, date, total, tax, description)
  slip     — pickup-slip extraction for house_account agreement purchases
             (slip_ref, date, amount, tax_amount, total_amount, currency)
"""
import asyncio
import base64
import json
import logging
from typing import Literal, NoReturn

from app.core.config import settings

# ── Why every client.messages.create below goes through asyncio.to_thread ─────
#
# `anthropic.Anthropic` is the SYNCHRONOUS client: .messages.create() blocks the
# calling thread until the model answers. These three functions are `async def`
# and are awaited straight from the /ocr/{mode} handler, so calling it inline
# blocked the whole asyncio event loop — for the 5-20 seconds a haiku vision
# read of a multi-page PDF takes, expense-api served NOBODY. Not the task list,
# not an approval, not an attachment download; a couple of people scanning
# receipts at once read as "OA is down".
#
# to_thread hands the blocking call to the default executor and yields the loop
# back. Exceptions propagate unchanged, so the BadRequestError / APIStatusError
# handling below still works exactly as written. (AsyncAnthropic would also do,
# but it changes the client's construction and error surface; this keeps the
# diff to the call sites.)

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.75  # fields below this are flagged for user review

# Anthropic answers HTTP 400 for two unrelated things: "this document cannot be
# read" AND account-level refusals (spend limit reached, credit balance too
# low). Both arrive as BadRequestError, and treating them alike told a finance
# clerk their invoice was unreadable when the real cause was a billing ceiling
# — 2026-09-08: the account hit its usage limit, EVERY EPMS upload failed with
# "AI parsing failed", and the blameless file got the blame. The user re-scans,
# re-exports, tries another invoice, and never learns it is not about the file.
#
# So split them. An account problem is an outage: RuntimeError → HTTP 503, with
# a message that says out loud "not your file" and names who can fix it. Only a
# genuine decode failure keeps the old ValueError → HTTP 422 "enter it manually".
#
# Matched on the message text because the payload carries no machine-readable
# distinction — both are type "invalid_request_error". Unmatched 400s keep the
# old document-failure reading: that is the far more common case, and a file
# error mislabelled as an outage would send people to IT for a bad scan.
_ACCOUNT_LIMIT_MARKERS = (
    "usage limit",
    "credit balance",
    "quota",
    "billing",
    "spend limit",
    "insufficient",
    "organization",
)


def _raise_for_bad_request(exc: Exception, mode: str) -> NoReturn:
    """Translate a Claude 400 into either an outage or a document failure."""
    message = str(getattr(exc, "message", "") or exc)
    if any(marker in message.lower() for marker in _ACCOUNT_LIMIT_MARKERS):
        log.error("%s OCR blocked by an account-level API limit: %s", mode, message)
        raise RuntimeError(
            "AI extraction is temporarily unavailable — the AI service account has "
            "reached a usage or billing limit. This is NOT a problem with your file; "
            "enter the details manually and let IT know. "
            f"(provider said: {message})"
        ) from exc
    log.warning("%s OCR could not process the file: %s", mode, exc)
    raise ValueError("Could not read this document. Please enter the details manually.") from exc


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
  "other_charges": {"value": number or null, "label": "string or null", "confidence": 0.0-1.0},
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
- EVERY line belongs in that sum, including lines that carry NO tax. A zero-tax
  line (exempt goods, freight billed as a line, a non-taxable service) counts
  towards "subtotal" exactly like a taxed one. Never sum only the taxed lines.
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
- "other_charges": any amount printed between the subtotal and the grand total
  that is NEITHER a line item NOR a tax — freight / shipping / delivery, fuel
  surcharge, handling, late fee / finance charge / interest, environmental or
  recycling fee, deposit, rounding. Add them together into one number and put
  what they are in "label" (e.g. "Freight", "Late fee", "Freight + handling").
  Null when the invoice has none. If such a charge is already one of the rows
  you returned in "line_items", it is a line item — leave "other_charges" null
  so it is not counted twice.
- "total_amount": the grand total EXACTLY as printed on the document. Never
  recompute it and never leave it null when a total is printed — it is what the
  application checks the other figures against.
- Before answering, verify that subtotal + tax_amount + other_charges equals
  total_amount. If it does not, you have missed a line, a charge or a tax:
  re-read the document and correct the figures.
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

_SLIP_PROMPT = """\
Extract pickup-slip information from this image.

Return ONLY this JSON (no markdown):
{
  "vendor_name":  {"value": "string or null", "confidence": 0.0-1.0},
  "slip_ref":     {"value": "string or null", "confidence": 0.0-1.0},
  "date":         {"value": "YYYY-MM-DD or null", "confidence": 0.0-1.0},
  "amount":       {"value": number or null, "confidence": 0.0-1.0},
  "tax_amount":   {"value": number or null, "confidence": 0.0-1.0},
  "total_amount": {"value": number or null, "confidence": 0.0-1.0},
  "currency":     {"value": "CAD", "confidence": 0.0-1.0}
}

- "vendor_name": the merchant name printed at the top of the slip — the store
  or company that issued it, as printed (e.g. "PRINCESS AUTO #12"). Do NOT
  translate, expand or tidy it. If no merchant name is legible, return null —
  this is normal and not an error; a person can fill it in afterwards.
- "slip_ref": the transaction or receipt reference printed on the slip. If the
  slip prints it as several separate fields (for example a till number and a
  transaction number in adjacent columns), join them with a hyphen in the order
  they appear. If no such reference is printed, return null — this is normal and
  not an error.
- "amount" is the PRE-TAX subtotal; "total_amount" is the amount actually
  charged, tax included."""


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


# Every total comparison below is done on integer CENTS. 109.00 + 14.17 in binary
# float is 123.17000000000002, and a difference of 2e-14 must never be shown to a
# clerk as "the total does not match". Two cents of slack absorbs the rounding a
# vendor's own printer does; anything wider is a real discrepancy.
_TOTAL_TOLERANCE_CENTS = 2


def _cents(value: float | None) -> int | None:
    return None if value is None else round(value * 100)


def _reconcile_totals(result: dict, lines: list[dict]) -> dict:
    """Check the header figures against the grand total printed on the invoice.

    EPMS stores exactly two money fields per invoice and derives the third:
    ``total_amount = amount + tax_amount`` (epms-api ``crud/invoice.py``). So any
    money the extraction leaves outside ``subtotal``/``tax_amount`` is money the
    vendor is never paid, and nothing downstream notices — the invoice matches,
    gets approved and gets paid short. Two shapes did exactly that in production:

    1. **Mixed taxed / untaxed lines.** The model sums the taxed rows, calls that
       the subtotal, and the zero-tax row falls out of the header. The line list is
       still complete, so the repair is provable: if Σ lines + tax + charges hits the
       printed grand total and the reported subtotal does not, the subtotal was wrong.

    2. **A charge under the subtotal** — freight, late fee, handling. It is neither
       a line nor a tax, so it had nowhere to go and was dropped. Folded into the
       pre-tax amount and itemised as a line, so the payable equals the document and
       the operator can see where the extra money came from. Only when the arithmetic
       proves it is *outside* the subtotal (``subtotal + tax + charge == total`` and
       ``subtotal + tax != total``) — a freight figure the model also included in the
       subtotal must not be counted twice.

    Anything left over is NOT guessed at. A residual could equally be a misread tax,
    a misread total or a line that never got extracted, and silently moving it into
    the pre-tax amount would fix the payable while corrupting the tax. It is reported
    as ``status: "mismatch"`` with the numbers, and the uploader decides. Same for a
    charge on an invoice whose grand total could not be read: with nothing to check
    against, "billed on top" and "already one of the lines" are indistinguishable, so
    it goes into ``notes`` for a person rather than being folded on a coin flip.

    Returns the ``total_check`` block; mutates ``result["subtotal"]`` and ``lines``
    when a repair fires.
    """
    def _num(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    subtotal = _cents(_num(result.get("subtotal")))
    tax = _cents(_num(result.get("tax_amount"))) or 0
    charges = _cents(_num(result.get("other_charges"))) or 0
    doc_total = _cents(_num(result.get("total_amount")))
    line_sum = sum(round(li["amount"] * 100) for li in lines) if lines else None

    def _near(a: int, b: int) -> bool:
        return abs(a - b) <= _TOTAL_TOLERANCE_CENTS

    repairs: list[str] = []   # what was changed
    notes: list[str] = []     # what could not be settled here, for a person to check

    # No readable subtotal — derive one rather than leave the form's required
    # Amount field blank. The lines are the better source when they exist; the
    # printed total minus what sits on top of it is the fallback.
    if subtotal is None:
        if line_sum is not None:
            subtotal = line_sum
            repairs.append("Pre-tax amount taken from the sum of the line items "
                           "(no subtotal could be read from the document).")
        elif doc_total is not None:
            subtotal = doc_total - tax - charges
            repairs.append("Pre-tax amount derived from the invoice total minus tax "
                           "and charges (no subtotal could be read from the document).")
        else:
            subtotal = 0

    # (1) lines the model left out of its own subtotal
    if (doc_total is not None and line_sum is not None
            and not _near(subtotal, line_sum)
            and _near(line_sum + tax + charges, doc_total)):
        subtotal = line_sum
        repairs.append("Pre-tax amount corrected to the sum of all line items — the "
                       "extracted subtotal had left one or more lines out.")

    # (2) a charge printed under the subtotal, outside both the lines and the tax
    if charges:
        # The printed grand total is the only thing that settles whether the
        # charge sits outside the subtotal: adding it has to be what reaches the
        # total. Without one the two readings are indistinguishable — a charge
        # billed on top of a 1000 subtotal and a charge already counted as one of
        # the lines both leave the line sum equal to the subtotal — so it is
        # flagged for a person instead of folded on a coin flip.
        outside = (doc_total is not None
                   and _near(subtotal + tax + charges, doc_total)
                   and not _near(subtotal + tax, doc_total))
        if doc_total is None:
            notes.append(
                f"The invoice also shows {result.get('other_charges_label') or 'other charges'} "
                f"of {charges / 100:.2f}, and no grand total could be read to check against — "
                "make sure the amount includes it.")
        if outside:
            label = result.get("other_charges_label") or "Other charges"
            subtotal += charges
            lines.append({
                "line_number": len(lines) + 1,
                "description": label,
                "quantity": 1.0,
                "unit_price": charges / 100,
                "amount": charges / 100,
                "tax_amount": 0.0,
            })
            repairs.append(
                f"{label} of {charges / 100:.2f} added to the pre-tax amount as a line "
                "item — the invoice bills it on top of the subtotal.")

    result["subtotal"] = subtotal / 100
    computed = subtotal + tax   # what EPMS will store as the invoice total

    if doc_total is None:
        status, difference = "unverified", None
    else:
        difference = (doc_total - computed) / 100
        status = "mismatch" if abs(doc_total - computed) > _TOTAL_TOLERANCE_CENTS else (
            "repaired" if repairs else "ok")

    if status == "mismatch":
        log.warning(
            "Invoice OCR total check failed: document total %.2f vs amount+tax %.2f "
            "(difference %.2f; lines %s, tax %.2f, charges %.2f)",
            doc_total / 100, computed / 100, difference,
            "n/a" if line_sum is None else f"{line_sum / 100:.2f}", tax / 100, charges / 100,
        )

    return {
        "status": status,                                  # ok | repaired | mismatch | unverified
        "document_total": None if doc_total is None else doc_total / 100,
        "computed_total": computed / 100,
        "line_sum": None if line_sum is None else line_sum / 100,
        "other_charges": charges / 100,
        "difference": difference,                          # document - computed, None if unverified
        "repairs": repairs,                                # what was changed, in plain English
        "notes": notes,                                    # what a person still has to check
    }


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
                     "currency", "subtotal", "tax_amount", "other_charges",
                     "total_amount"]

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

    # What the charge under the subtotal is called on the document ("Freight",
    # "Late fee"). Rides in the same envelope as the amount; carried out flat so
    # the folded line item can be named after it instead of "Other charges".
    oc_item = parsed.get("other_charges", {})
    oc_label = oc_item.get("label") if isinstance(oc_item, dict) else None
    result["other_charges_label"] = str(oc_label).strip() if oc_label else None

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
    # Must run last: it reads the line amounts the two repairs above settle on,
    # and may append a line of its own for a charge billed outside the subtotal.
    result["total_check"] = _reconcile_totals(result, lines)

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
            currency, subtotal, tax_amount, other_charges, total_amount,
            line_items: [...],
            ocr_confidence: float,          # average confidence
            low_confidence_fields: [str],   # fields below threshold
            total_check: {...},             # see _reconcile_totals
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
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            messages=[{
                "role": "user",
                "content": [content_block, {"type": "text", "text": _INVOICE_PROMPT}],
            }],
        )
    except anthropic.BadRequestError as exc:
        _raise_for_bad_request(exc, "Invoice")
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
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": [content_block, {"type": "text", "text": _RECEIPT_PROMPT}]}],
        )
    except anthropic.BadRequestError as exc:
        # Unreadable / unsupported file (e.g. HEIC, corrupt) — degrade to manual
        # entry (caller maps ValueError → HTTP 422); an account-level limit is an
        # outage instead. See _raise_for_bad_request.
        _raise_for_bad_request(exc, "Receipt")
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


def _slip_field(parsed: dict, key: str, default=None):
    """Unwrap one ``{"value": ..., "confidence": ...}`` pair from a slip response.

    Review round 1 (Minor 2): the direct form, ``parsed.get(k, {}).get("value")``,
    survives a MISSING key but not a key whose value is JSON ``null`` — the
    default never fires, ``.get`` lands on ``None``, and the AttributeError
    takes down the whole extraction with a 500. `_SLIP_PROMPT` explicitly
    invites nulls ("return null — this is normal and not an error"), so a model
    that answers ``{"vendor_name": null}`` instead of
    ``{"vendor_name": {"value": null}}`` is a well-behaved model, and it must
    not cost the recorder their OCR.

    Behaviour is otherwise byte-identical to what it replaces: a missing key
    yields ``default``, a present pair yields its ``value`` (``default`` when
    the pair itself omits one), and an explicit ``"value": null`` still yields
    ``None`` — including for ``currency``, whose "CAD" default has always
    applied to the key/field being absent, not to a null value.

    Scoped to extract_slip on purpose. extract_invoice/extract_receipt read
    their fields the same fragile way, but they serve OA expense claims and
    invoice OCR in production and are out of this task's blast radius.
    """
    field = parsed.get(key)
    if not isinstance(field, dict):
        return default
    return field.get("value", default)


async def extract_slip(file_bytes: bytes, mime_type: str) -> dict:
    """Pickup-slip OCR for house_account agreement purchases.

    A missing ``slip_ref`` is normal (baseline matching keys off date + amount,
    not the reference), so it is returned as ``None`` rather than treated as
    an extraction failure.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    media_type = _mime_to_media_type(mime_type)

    # PDF slips must be sent as a document block; images as an image block.
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
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": [content_block, {"type": "text", "text": _SLIP_PROMPT}]}],
        )
    except anthropic.BadRequestError as exc:
        # Unreadable / unsupported file (e.g. HEIC, corrupt) — degrade to manual
        # entry (caller maps ValueError → HTTP 422); an account-level limit is an
        # outage instead. See _raise_for_bad_request.
        _raise_for_bad_request(exc, "Slip")
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

    amount = _slip_field(parsed, "amount")
    tax_amount = _slip_field(parsed, "tax_amount")
    total_amount = _slip_field(parsed, "total_amount")

    # Whole-branch review (small item A): counter slips very often print only a
    # subtotal and a total, no separate tax line — and the prompt explicitly
    # allows null for every field. The EPMS slip form requires all three
    # amounts and told the user "Amount, tax and total are all required"
    # without hinting that 0, or total − amount, is what belongs there. Derive
    # it: the row's own invariant is amount + tax == total, so with two of the
    # three known the third is not a guess.
    #
    # Integer cents, not float subtraction: the backend validates
    # amount + tax_amount == total_amount as exact Decimal equality against a
    # Numeric(15,2) column, and 113.0 - 100.0 in binary float is
    # 13.000000000000014 — which serialises into the form, fails that equality
    # and 422s. (Same reasoning as centsEqual in SlipEntryForm.tsx.)
    if tax_amount is None and amount is not None and total_amount is not None:
        try:
            derived_cents = round(float(total_amount) * 100) - round(float(amount) * 100)
        except (TypeError, ValueError):
            derived_cents = None
        # A negative difference means OCR misread one of the two figures;
        # leave tax null rather than prefilling a value that cannot be right.
        if derived_cents is not None and derived_cents >= 0:
            tax_amount = derived_cents / 100

    return {
        # The merchant printed on the slip — NOT the agreement's vendor. epms
        # stores it and flags the two disagreeing (a slip from shop A recorded
        # against shop B's house account is the classic house-account
        # mis-posting), so a null here must stay null rather than being
        # back-filled with a guess.
        "vendor_name": _slip_field(parsed, "vendor_name"),
        "slip_ref": _slip_field(parsed, "slip_ref"),
        "date": _slip_field(parsed, "date"),
        "amount": amount,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "currency": _slip_field(parsed, "currency", "CAD"),
    }
