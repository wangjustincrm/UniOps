"""TODO(Task 3): delete this shim.

Same reasoning as app/models/agreement_slip.py: kept only so
app/api/v1/agreement_slip_attachments.py — which still
`from app.models.agreement_slip_attachment import AgreementSlipAttachment` —
continues to import successfully after the Task 1 rename to
app/models/agreement_receipt_attachment.py::AgreementReceiptAttachment.
"""
from app.models.agreement_receipt_attachment import AgreementSlipAttachment  # noqa: F401
