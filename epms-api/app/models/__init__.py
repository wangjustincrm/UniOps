# Import all model modules here so that Alembic env.py can discover them.
from app.models.agreement import PurchaseAgreement  # noqa: F401
from app.models.agreement_attachment import AgreementAttachment  # noqa: F401
from app.models.agreement_schedule import AgreementPaymentSchedule  # noqa: F401
from app.models.agreement_receipt import AgreementReceipt  # noqa: F401
from app.models.agreement_receipt_attachment import AgreementReceiptAttachment  # noqa: F401
from app.models.approval import ApprovalEvent  # noqa: F401
from app.models.config import CompanyConfig  # noqa: F401
# NOTE: BudgetAccount / BudgetL1 moved to budget-api (:8007). No mirror in epms-api.
from app.models.cost_center import CostCenter  # noqa: F401
from app.models.department import Department  # noqa: F401
from app.models.gr import GoodsReceipt, GrLineItem  # noqa: F401
from app.models.invoice import Invoice  # noqa: F401
from app.models.invoice_allocation import InvoicePoAllocation  # noqa: F401
from app.models.invoice_tax_line import InvoiceTaxLine  # noqa: F401
from app.models.nc_purchase_sync import NcPurchaseSyncRun  # noqa: F401
from app.models.pa import PaLineItem, PaymentApplication  # noqa: F401
from app.models.part import Part  # noqa: F401
from app.models.po import PoLineItem, PurchaseOrder  # noqa: F401
from app.models.po_signoff_signature import PoSignoffSignature  # noqa: F401
from app.models.pr import PrLineItem, PurchaseRequest  # noqa: F401
from app.models.notification_log import NotificationLog  # noqa: F401
from app.models.pr_attachment import PrAttachment  # noqa: F401
from app.models.gr_attachment import GrAttachment  # noqa: F401
from app.models.pa_attachment import PaAttachment  # noqa: F401
from app.models.pa_po_link import PaPoLink  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.task import Task  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.vendor import Vendor  # noqa: F401
from app.models.admin_audit_log import AdminAuditLog  # noqa: F401
