"""TODO(Task 3): delete this shim.

Task 2 moved the real CRUD to app/crud/agreement_receipt.py (create /
list_for_agreement / update / void / ap_review / claim, plus the EDITABLE /
VOIDABLE / RETIRED constants) and renamed claim()'s `slip_ids` parameter to
`receipt_ids` (positional-only callers, e.g. app/crud/invoice.py, are
unaffected). This file is kept only so app/api/v1/invoices.py — which still
`from app.crud import agreement_slip as agreement_slip_crud` — continues to
import successfully. Task 3 moves it onto agreement_receipt directly and
deletes this file (see app/models/agreement_slip.py for the same reasoning
one layer down).
"""
from app.crud.agreement_receipt import (  # noqa: F401
    EDITABLE,
    RETIRED,
    VOIDABLE,
    ap_review,
    claim,
    create,
    list_for_agreement,
    update,
    void,
)
