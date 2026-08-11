"""TODO(Task 3): delete this shim.

Task 2 moved the real schemas to app/schemas/agreement_receipt.py (ReceiptCreate /
ReceiptUpdate / ReceiptResponse / ReceiptListResponse / ReceiptApReview /
validate_totals) and generalised ReceiptCreate/ReceiptUpdate with a validated
`receipt_type` field. This file is kept only so app/api/v1/invoices.py — which
still `from app.schemas.agreement_slip import SlipListResponse` — continues to
import successfully. Task 3 moves it onto agreement_receipt directly (and, per
Task 1's precedent, deletes app/api/v1/agreement_slips.py's own copy of this
same import once that router is renamed) and deletes this file.

⚠️ Field names changed on the real schemas: slip_date -> receipt_date,
slip_ref -> receipt_ref, picked_by -> received_by, missing_slip_reason ->
missing_receipt_reason. Code reached through these aliases that still builds a
payload with the old field names will 422 (ReceiptCreate/ReceiptUpdate have no
such fields) — that is expected until Task 3 lands, not a regression
introduced here.
"""
from app.schemas.agreement_receipt import (  # noqa: F401
    ReceiptApReview as SlipApReview,
    ReceiptCreate as SlipCreate,
    ReceiptListResponse as SlipListResponse,
    ReceiptResponse as SlipResponse,
    ReceiptUpdate as SlipUpdate,
    validate_totals,
)
