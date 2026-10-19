"""Courier invoice and waybill reading for EPMS Freight.

Design: docs/superpowers/specs/2026-10-19-freight-payment-design.md §3.5 —
written against three real invoices (UPS 00004E4F77416, FedEx 2-749-42565,
Purolator 550316833). What makes them hard, and what the prompt says about it:

  * the first page of a UPS bill prints BOTH "amount due this period" and
    "total amount outstanding" (prior unpaid invoices) — only the former is
    this invoice;
  * a shipment can have several packages (a lead number plus children); a
    buyer may have registered any of them, so every number is returned;
  * charges without a tracking number are routine (on-call pickups, late
    payment fees), not noise;
  * tax is printed per shipment (Purolator), per charge category (UPS: only
    the pickup requests carry HST), or not at all (FedEx inbound).

The model returns raw numbers; `assemble_freight_result` (pure, unit-tested)
spreads category tax onto lines and checks the lines against the header so
the reviewer sees at once whether anything was missed. Haiku only — the key
is shared with Claude Code (see config.py).
"""
import asyncio
import base64
import json
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.config import settings
from app.services.ocr_service import _mime_to_media_type, _raise_for_bad_request

log = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_CENT = Decimal("0.01")
_ZERO = Decimal("0")

_FREIGHT_PROMPT = """\
You are reading a courier's invoice (UPS, FedEx, Purolator, Canada Post, DHL or similar) for accounts payable.
Read EVERY page. Return ONLY a JSON object, no markdown, with exactly these keys:

{
  "carrier": "UPS" | "FEDEX" | "PUROLATOR" | "CANADA_POST" | "DHL" | "OTHER",
  "invoice_number": string,
  "account_number": string | null,
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD" | null,
  "currency": "CAD" | "USD" | ...,
  "amount_due": number,          // the amount due for THIS invoice only, tax included
  "subtotal": number,            // this invoice before tax
  "tax_total": number,           // all tax on this invoice (GST + HST + QST + PST)
  "tax_by_category": [           // only when tax is printed per charge category, not per shipment
    {"category": string, "taxable_amount": number, "tax": number}
  ],
  "lines": [
    {
      "charge_type": "shipment" | "pickup" | "fee" | "adjustment",
      "category": string | null,         // the section heading the line sits under, e.g. "Worldwide Service", "On-Call Pickup Requests", "Fees"
      "tracking_no": string | null,      // lead tracking / shipment / PIN number
      "package_tracking_nos": [string],  // EVERY package number of this shipment, lead included
      "reference": string | null,        // customer reference (1st ref, Ref.#2, Cust. Ref.) or pickup request / transaction number
      "ship_date": "YYYY-MM-DD" | null,  // pickup / ship / service date
      "description": string | null,      // service name or fee name; for shipments add "sender → receiver city"
      "amount": number,                  // this line's total BEFORE tax: transportation + fuel + surcharges − discounts
      "tax": number | null               // tax printed for THIS line only; null when not printed per line
    }
  ]
}

Rules:
- amount_due is THIS period only. Never use "total amount outstanding", "balance forward",
  "amount outstanding (prior invoices)" or "payments applied" — those belong to other invoices.
- One line per shipment, not per package and not per surcharge: fold fuel surcharge, residential
  delivery, discounts (negative) etc. into that shipment's amount.
- On-call / future-day pickup requests are charge_type "pickup" (one line per pickup request).
- Late payment fees, account fees and similar are charge_type "fee".
- Credits, refunds, corrections and audits of an earlier shipment are charge_type "adjustment" (amount may be negative).
- Copy tracking numbers exactly as printed, without spaces.
- Year: the invoice date gives the year when a line prints only month/day.
- Use null when a value is not printed. Numbers are plain numbers without currency symbols or thousands separators.
"""

_WAYBILL_PROMPT = """\
This is a photo or scan of a courier waybill / shipping label. Return ONLY a JSON object, no markdown:
{"carrier": "UPS" | "FEDEX" | "PUROLATOR" | "CANADA_POST" | "DHL" | "OTHER" | null,
 "tracking_numbers": [string]}
List every tracking / shipment / PIN number printed (barcode captions included), lead number first,
exactly as printed without spaces. Do not list account numbers, phone numbers or postal codes.
"""


def _d(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("$", "").strip()).quantize(_CENT, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _norm_tracking(v) -> str | None:
    if not v:
        return None
    s = re.sub(r"[\s\-.]", "", str(v)).upper()
    return s or None


def _spread(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    n = len(weights)
    if n == 0:
        return []
    base = sum(weights)
    if base == 0:
        weights, base = [Decimal(1)] * n, Decimal(n)
    parts = [(total * w / base).quantize(_CENT, ROUND_HALF_UP) for w in weights]
    diff = total - sum(parts)
    if diff:
        parts[max(range(n), key=lambda k: weights[k])] += diff
    return parts


def _same_category(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or a in b or b in a


def assemble_freight_result(parsed: dict) -> dict:
    """Turn the model's JSON into what the Freight upload form pre-fills.

    Tax (design D13): a line keeps the tax printed for it. Tax printed per
    category is spread over that category's lines by amount. Whatever is still
    unattributed is reported (epms spreads it over all lines on save).
    """
    warnings: list[str] = []
    lines = []
    for raw in parsed.get("lines") or []:
        amt = _d(raw.get("amount"))
        if amt is None:
            warnings.append(f"Skipped a line with no amount: {raw.get('description') or raw}")
            continue
        lead = _norm_tracking(raw.get("tracking_no"))
        pkgs = [p for p in (_norm_tracking(x) for x in raw.get("package_tracking_nos") or []) if p]
        if lead and lead not in pkgs:
            pkgs.insert(0, lead)
        ctype = raw.get("charge_type") if raw.get("charge_type") in (
            "shipment", "pickup", "fee", "adjustment") else ("shipment" if lead else "fee")
        tax = _d(raw.get("tax"))
        lines.append({
            "charge_type": ctype, "category": raw.get("category"),
            "tracking_no": lead or (pkgs[0] if pkgs else None),
            "package_tracking_nos": pkgs, "reference": raw.get("reference"),
            "ship_date": raw.get("ship_date"), "description": raw.get("description"),
            "amount": amt, "tax_amount": tax if tax is not None else _ZERO,
            "_tax_printed": tax is not None,
        })

    # Per-line tax that already accounts for the whole tax total wins: the model
    # sometimes also lists the summary page's "Total GST / Total HST" as
    # categories (Purolator 550316833), which would be counted twice.
    printed = sum((l["tax_amount"] for l in lines if l["_tax_printed"]), _ZERO)
    categories = [] if printed and printed == (_d(parsed.get("tax_total")) or _ZERO) \
        else (parsed.get("tax_by_category") or [])
    for cat in categories:
        tax = _d(cat.get("tax"))
        if not tax:
            continue
        targets = [l for l in lines if not l["_tax_printed"]
                   and _same_category(l["category"], cat.get("category"))]
        if not targets:
            warnings.append(f"Tax {tax} for '{cat.get('category')}' matched no line; "
                            f"it will be spread over all lines.")
            continue
        for l, part in zip(targets, _spread(tax, [l["amount"] for l in targets])):
            l["tax_amount"] += part

    subtotal = _d(parsed.get("subtotal"))
    tax_total = _d(parsed.get("tax_total")) or _ZERO
    amount_due = _d(parsed.get("amount_due"))
    lines_amount = sum((l["amount"] for l in lines), _ZERO)
    lines_tax = sum((l["tax_amount"] for l in lines), _ZERO)
    if subtotal is None and amount_due is not None:
        subtotal = amount_due - tax_total

    def _c(v: Decimal | None) -> str | None:
        return None if v is None else str(v.quantize(_CENT))

    checks = {
        "lines_amount": _c(lines_amount), "lines_tax": _c(lines_tax),
        "subtotal": _c(subtotal), "tax_total": _c(tax_total), "amount_due": _c(amount_due),
        "lines_match_subtotal": subtotal is not None and lines_amount == subtotal,
        "header_adds_up": (amount_due is None or subtotal is None
                           or subtotal + tax_total == amount_due),
        "unattributed_tax": _c(tax_total - lines_tax),
    }
    if not checks["lines_match_subtotal"]:
        warnings.append(f"The lines add up to {lines_amount} before tax but the invoice says "
                        f"{subtotal}. Check for a missed or misread line.")
    if not checks["header_adds_up"]:
        warnings.append(f"Subtotal {subtotal} + tax {tax_total} does not equal the amount due {amount_due}.")

    for l in lines:
        l.pop("_tax_printed")
        l.pop("category")
        l["amount"] = str(l["amount"].quantize(_CENT))
        l["tax_amount"] = str(l["tax_amount"].quantize(_CENT))
    return {
        "carrier": parsed.get("carrier"),
        "invoice_number": (parsed.get("invoice_number") or "").strip() or None,
        "account_number": parsed.get("account_number"),
        "invoice_date": parsed.get("invoice_date"),
        "due_date": parsed.get("due_date"),
        "currency": parsed.get("currency") or "CAD",
        "amount": _c(subtotal),
        "tax_amount": _c(tax_total),
        "lines": lines,
        "checks": checks,
        "status": "ok" if checks["lines_match_subtotal"] and checks["header_adds_up"] else "mismatch",
        "warnings": warnings,
    }


def _parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("Freight OCR JSON parse error: %s\nRaw: %s", exc, raw[:500])
        raise ValueError("Could not read this document. Please enter the details manually.")


async def _ask(file_bytes: bytes, mime_type: str, prompt: str, max_tokens: int, label: str) -> dict:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured — OCR is unavailable")
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    media_type = _mime_to_media_type(mime_type)
    block = ({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
             if media_type == "application/pdf" else
             {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
    try:
        # to_thread: the client is synchronous (see ocr_service's header note).
        response = await asyncio.to_thread(
            client.messages.create, model=_MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": [block, {"type": "text", "text": prompt}]}])
    except anthropic.BadRequestError as exc:
        _raise_for_bad_request(exc, label)
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        raise RuntimeError(f"OCR service error: {exc}")
    if response.stop_reason == "max_tokens":
        raise ValueError("The invoice has more lines than can be read at once. Enter it manually "
                         "or import the carrier's detail file.")
    return _parse_json(response.content[0].text)


async def extract_freight_invoice(file_bytes: bytes, mime_type: str) -> dict:
    return assemble_freight_result(
        await _ask(file_bytes, mime_type, _FREIGHT_PROMPT, 8192, "Freight invoice"))


async def extract_waybill(file_bytes: bytes, mime_type: str) -> dict:
    parsed = await _ask(file_bytes, mime_type, _WAYBILL_PROMPT, 512, "Waybill")
    nums = [n for n in (_norm_tracking(x) for x in parsed.get("tracking_numbers") or []) if n]
    return {"carrier": parsed.get("carrier"), "tracking_numbers": list(dict.fromkeys(nums))}
