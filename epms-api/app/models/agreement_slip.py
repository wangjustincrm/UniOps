"""TODO(Task 3): delete this shim.

Task 1 renamed the real model to app/models/agreement_receipt.py::AgreementReceipt
and generalised it (receipt_type: counter_slip | delivery | service). This file
is kept only so app/crud/agreement_slip.py, app/api/v1/agreement_slips.py and
app/api/v1/invoices.py — which still `from app.models.agreement_slip import
AgreementPickupSlip` — continue to import successfully. Those layers are
explicitly out of scope for Task 1; Task 2/3 move them onto AgreementReceipt
and delete this file.

⚠️ Column names changed on the real model: slip_date -> receipt_date,
slip_ref -> receipt_ref, picked_by -> received_by, missing_slip_reason ->
missing_receipt_reason. Code reached through this alias that still uses the
old attribute names will raise AttributeError/TypeError at call time — that
is expected until Task 2/3 land, not a regression introduced here.
"""
from app.models.agreement_receipt import AgreementPickupSlip  # noqa: F401
