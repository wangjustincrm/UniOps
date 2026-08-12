"""Server-side OCR endpoint — keeps the Anthropic API key off the client.

POST /api/v1/ocr/{mode}  (mode = invoice | receipt | slip), multipart file upload.
  invoice — full extraction for PA-DIR (vendor, invoice_no, dates, line_items, totals)
  receipt — simple extraction for EXP/TRV line items (vendor, date, total, tax)
  slip    — pickup-slip extraction for house_account agreement purchases
            (vendor_name, slip_ref, date, amount, tax_amount, total_amount,
             currency)

Replaces the previous client-side extraction in oa/src/lib/invoice-parser.ts,
which exposed VITE_ANTHROPIC_API_KEY in the browser bundle.
"""
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.core.deps import CurrentUserDep
from app.services import ocr_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ocr", tags=["ocr"])

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB
SUPPORTED_MIME = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp",
    "application/pdf",
}


@router.post("/{mode}")
async def run_ocr(
    mode: str,
    _: CurrentUserDep,
    file: UploadFile = File(...),
):
    """Extract structured data from an uploaded invoice/receipt via Claude vision."""
    if mode not in ("invoice", "receipt", "slip"):
        raise HTTPException(status_code=400, detail="mode must be 'invoice', 'receipt' or 'slip'")

    content_type = (file.content_type or "").lower()
    if content_type not in SUPPORTED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported format '{content_type}'. Use JPEG, PNG, WebP, GIF or PDF.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")

    try:
        if mode == "invoice":
            return await ocr_service.extract_invoice(data, content_type)
        if mode == "slip":
            return await ocr_service.extract_slip(data, content_type)
        return await ocr_service.extract_receipt(data, content_type)
    except RuntimeError as exc:
        # API unavailable / not configured — degrade gracefully (client allows manual entry)
        log.error("OCR (%s) unavailable: %s", mode, exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        # Unparseable response — treat as low-confidence read
        log.error("OCR (%s) parse failure: %s", mode, exc)
        raise HTTPException(status_code=422, detail="Could not read document clearly. Please enter details manually.")
