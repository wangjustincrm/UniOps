"""CRUD operations for Company Config."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.config import CompanyConfig
from app.schemas.config import (
    ConfigUpdate,
    CustomRoleCreate, CustomRoleUpdate, RolePermissionsUpdate,
)

# ── RBAC constants ───────────────────────────────────────────────────────────

# Built-in system role codes — cannot be deleted or deactivated
BUILT_IN_ROLES: frozenset[str] = frozenset({
    "requester", "dept_admin", "dept_manager", "gm", "opm",
    "procurement_officer", "procurement_manager", "warehouse_staff",
    "ap_clerk", "finance_bp", "finance_manager", "vendor_manager",
    "cfo", "auditor", "erp_pa_officer", "payment_officer", "system_admin",
})

# Roles granted ONLY through identity's user_roles side table — never written to
# users.role. identity's role_defs.assignable_as_primary (migration 0009) is the
# source of truth; this mirror only backs the identity-down fallback below, so
# the primary-role guard does not come undone when identity is unreachable.
ADDITIONAL_ONLY_ROLES: frozenset[str] = frozenset({"erp_pa_officer", "payment_officer"})

# Permissions that cannot be disabled for the given role (enforced server-side).
# The view_* locks below correspond to roles that would be functionally broken
# without that visibility (e.g. ap_clerk must see invoices to match them).
LOCKED_PERMISSIONS: dict[str, set[str]] = {
    "requester":            {"view_pr"},
    "procurement_officer":  {"view_pr", "view_po", "view_gr"},
    "procurement_manager":  {"view_pr", "view_po", "view_gr"},
    "warehouse_staff":      {"view_gr"},
    "ap_clerk":             {"view_invoice", "view_pa"},
    "finance_bp":           {"view_pa"},
    "finance_manager":      {"view_pa"},
    "erp_pa_officer":       {"view_po", "view_pa"},
    "payment_officer":      {"view_pa"},
    "system_admin":         {"admin_panel"},
}

# Permission column keys (fixed order for the UI)
# view_* keys gate visibility of the 5 EPMS list/detail endpoints in addition to
# the role-based scope rules in app/core/access_scope.py.
PERMISSION_KEYS: list[str] = [
    "view_pr", "view_po", "view_gr", "view_invoice", "view_pa",
    "create_pr", "create_gr", "invoice_upload",
    "vendor_master", "parts_catalog", "admin_panel",
    "data_maintenance",
    # 无收货 override:允许在无 3-way matched 发票时强制建 PA(带理由,落 PA 审计)。
    "pa_override_receipt",
    # Portal Finance sidebar visibility. view_budget_dashboard / view_budget_plans
    # gate the two cross-module budget views (also visible to dept managers);
    # view_finance gates the rest of the FINANCE section (Account Catalog, Factor
    # Library, Budget Config, CoA, Bank Recon, Payment Batches, AR, GL).
    "view_budget_dashboard", "view_budget_plans", "view_finance",
    # Booking module (meeting rooms). view_booking gates the whole employee-facing
    # module; manage_meeting_rooms gates room CRUD / all-bookings admin.
    "view_booking", "manage_meeting_rooms",
]

# ── Default values seeded on first access ───────────────────────────────────

_DEFAULT_WORKFLOW_CONFIG = {
    "escalation_threshold_cad": 60000,
    "po_low_value_bypass_enabled": False,
    "po_low_value_bypass_cad": 5000,
    "approval_reminder_days": 2,
    "approval_auto_escalation_days": 5,
    # NOTE: `over_budget_mode` lives on BudgetAdminConfig (Admin → Budget Config).
    # Removed from WorkflowConfig in 2026-05 to keep a single source of truth.
    "consolidate_gm_opm_approval": True,
}

_DEFAULT_PDF_TEMPLATES = {
    "pr": {"show_logo": True, "header_note": "", "footer_note": "", "show_terms": False, "terms_text": ""},
    "po": {"show_logo": True, "header_note": "", "footer_note": "", "show_terms": True,  "terms_text": "Standard terms and conditions apply."},
    "gr": {"show_logo": True, "header_note": "", "footer_note": "", "show_terms": False, "terms_text": ""},
    "pa": {"show_logo": True, "header_note": "", "footer_note": "", "show_terms": False, "terms_text": ""},
}

_DEFAULT_SERVICE_GR_SLA = {"reminder_days": 1, "manager_escalation_days": 3}

_DEFAULT_GR_NOTIFICATION_SLA = {"reminder_days": 1, "manager_escalation_days": 3}

_DEFAULT_PREPAYMENT_CONFIG = {
    "max_prepayment_pct": 100, "settlement_sla_days": 5,
    "settlement_manager_escalation_days": 3, "settlement_gm_opm_escalation_days": 5,
    "block_po_closure_on_unsettled": True,
}

_DEFAULT_BUDGET_ADMIN_CONFIG = {
    "yellow_threshold_pct": 80, "red_threshold_pct": 100, "over_budget_mode": "fm_gm_opm",
    "available_fiscal_years": [2024, 2025, 2026, 2027],
}

_DEFAULT_COLLECTION_CONFIG = {
    "collection_required": True, "reminder_days": 2,
    "manager_escalation_days": 4, "fm_alert_days": 7,
}

_DEFAULT_NOTIFICATION_SETTINGS = {
    "default_channel": "email_only",   # email_only | teams_only | both | none
    "teams_webhook_url": None,
    "followup_time": "08:00",
    # 每日 follow-up 提醒总开关(Portal → Admin → Notification Settings)。
    # 默认关;消费方一律 .get(..., False) 显式回落,老配置行没有该键也算关。
    "daily_followup_enabled": False,
    # 默认 ON 是刻意的:daily_followup_enabled 默认 OFF,结果上线后没人知道要去
    # admin 打开、提醒一直没发。开关的作用是"吵了可以关掉",不是"要用得先找到它"。
    "agreement_overdue_enabled": True,
    # 服务/项目 PO 完成日到期催建 GR(app/tasks/service_gr_due.py)。
    # 默认 OFF + dry_run 默认 ON 是刻意的两道闸:上线当天先让扫描空跑一轮,
    # 把命中清单汇总发给 admin 过目,确认无误再开。存量单据靠回填脚本补日期,
    # 一次放开可能同时命中一大批逾期 PO —— 2026-08-05 的 GR 群发 59 人就是
    # 没有这道闸。消费方一律显式回落,老配置行没有这两个键也算「关 + 空跑」。
    "service_gr_due_enabled": False,
    "service_gr_due_dry_run": True,
    # 角色 → 共享邮箱。配了地址的角色,其“角色池”任务只发这一个邮箱,
    # 不再逐个通知该角色成员。空 = 维持逐人发送。
    "role_shared_mailboxes": {},
}

_DEFAULT_EMAIL_TEMPLATE = lambda subject, body: {"subject": subject, "body": body}  # noqa: E731

_DEFAULT_EMAIL_TEMPLATES: dict = {
    # ── PR ────────────────────────────────────────────────────────────────────
    "pr_approval_request": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Approve PR {pr_number}",
        "Hi {recipient_name},\n\nPurchase Request <b>{pr_number}</b> requires your approval.\n\n"
        "<b>Vendor:</b> {vendor}\n<b>Amount:</b> CAD {amount}\n\n"
        "<a href=\"{link}\">Review &amp; Approve PR</a>\n\n{company_name}",
    ),
    "pr_returned": _DEFAULT_EMAIL_TEMPLATE(
        "Your PR {pr_number} has been returned for revision",
        "Hi {recipient_name},\n\nYour Purchase Request <b>{pr_number}</b> has been returned for revision.\n\n"
        "<b>Comment:</b> {comment}\n\n<a href=\"{link}\">View &amp; Edit PR</a>\n\n{company_name}",
    ),
    "pr_rejected": _DEFAULT_EMAIL_TEMPLATE(
        "Your PR {pr_number} has been rejected",
        "Hi {recipient_name},\n\nYour Purchase Request <b>{pr_number}</b> has been rejected.\n\n"
        "<b>Comment:</b> {comment}\n\n<a href=\"{link}\">View PR</a>\n\n{company_name}",
    ),
    "pr_approved": _DEFAULT_EMAIL_TEMPLATE(
        "Your PR {pr_number} has been approved",
        "Hi {recipient_name},\n\nYour Purchase Request <b>{pr_number}</b> has been fully approved.\n\n"
        "<a href=\"{link}\">View PR</a>\n\n{company_name}",
    ),
    # ── PO ────────────────────────────────────────────────────────────────────
    "po_creation_request": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Create PO for PR {pr_number}",
        "Hi {recipient_name},\n\nPurchase Request <b>{pr_number}</b> has been fully approved. Please create a Purchase Order.\n\n"
        "<b>Vendor:</b> {vendor}\n<b>Amount:</b> CAD {amount}\n\n"
        "<a href=\"{link}\">View PR &amp; Create PO</a>\n\n{company_name}",
    ),
    "po_approval_request": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Approve PO {po_number}",
        "Hi {recipient_name},\n\nPurchase Order <b>{po_number}</b> requires your approval.\n\n"
        "<b>Vendor:</b> {vendor}\n<b>Amount:</b> CAD {amount}\n\n"
        "<a href=\"{link}\">Review &amp; Approve PO</a>\n\n{company_name}",
    ),
    # ── PO sign-off (NC-imported POs) ────────────────────────────────────────
    "po_signature_request": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Sign PO {po_number}",
        "Hi {recipient_name},\n\nPurchase Order <b>{po_number}</b> is waiting for your signature.\n\n"
        "<b>Vendor:</b> {vendor}\n<b>Amount:</b> CAD {amount}\n\n"
        "<a href=\"{link}\">Review &amp; Sign</a>\n\n{company_name}",
    ),
    "po_signoff_returned": _DEFAULT_EMAIL_TEMPLATE(
        "PO {po_number} sign-off returned for revision",
        "Hi {recipient_name},\n\nThe sign-off you raised for Purchase Order <b>{po_number}</b> "
        "has been returned.\n\n<b>Comment:</b> {comment}\n\n"
        "<a href=\"{link}\">View PO</a>\n\n{company_name}",
    ),
    # Sent to whoever raised the sign-off once every step has signed. This one
    # is NOT a task — nothing in UniOps can do the remaining step, which is to
    # release the order in NC.
    "po_signoff_complete": _DEFAULT_EMAIL_TEMPLATE(
        "PO {po_number} is fully signed — release it in NC",
        "Hi {recipient_name},\n\nPurchase Order <b>{po_number}</b> has been signed by every "
        "required signatory.\n\n<b>Signed by:</b> {signatories}\n\n"
        "You can now release the order in NC.\n\n"
        "<a href=\"{link}\">View PO</a>\n\n{company_name}",
    ),
    "po_place_order": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Place Order — {po_number}",
        "Hi {recipient_name},\n\nPurchase Order <b>{po_number}</b> has been fully approved and is ready to be placed with the vendor.\n\n"
        "<b>Vendor:</b> {vendor}\n<b>Amount:</b> CAD {amount}\n\n"
        "<a href=\"{link}\">Place Order</a>\n\n{company_name}",
    ),
    # ── GR ────────────────────────────────────────────────────────────────────
    "gr_created": _DEFAULT_EMAIL_TEMPLATE(
        "Goods Receipt Created — {gr_number}",
        "Hi {recipient_name},\n\nGoods Receipt <b>{gr_number}</b> has been created and requires your acknowledgement.\n\n"
        "<a href=\"{link}\">View GR</a>\n\n{company_name}",
    ),
    "gr_collection_ready": _DEFAULT_EMAIL_TEMPLATE(
        "Goods Ready for Collection — {gr_number}",
        "Hi {recipient_name},\n\nThe goods for GR <b>{gr_number}</b> are ready for collection.\n\n"
        "<a href=\"{link}\">Confirm Collection</a>\n\n{company_name}",
    ),
    "service_gr_pending": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Confirm Service Completion — {gr_number}",
        "Hi {recipient_name},\n\nService GR <b>{gr_number}</b> is pending your confirmation.\n\n"
        "<a href=\"{link}\">Confirm Service Completion</a>\n\n{company_name}",
    ),
    # 完成日到期催建 GR(service_gr_due 扫描)。与 service_gr_pending 的区别:
    # 那个是「GR 已经建好了,去确认」,这个是「还没有 GR,去建」,所以变量围绕
    # PO 而不是 GR,链接也直通 New GR 页(_task_link 对 confirm_receipt 特判)。
    "service_gr_due": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Confirm Service Completion — {po_number}",
        "Hi {recipient_name},\n\nThe expected completion date for PO <b>{po_number}</b> "
        "was <b>{completion_date}</b> ({days_overdue} day(s) ago), but no goods receipt "
        "has been created yet.\n\nIf the service is complete, please confirm it so the "
        "vendor can be paid.\n\n"
        "<b>Vendor:</b> {vendor}\n\n"
        "<a href=\"{link}\">Confirm Service &amp; Create GR</a>\n\n{company_name}",
    ),
    "service_gr_escalation": _DEFAULT_EMAIL_TEMPLATE(
        "Overdue: Service Completion Not Confirmed — {po_number}",
        "Hi {recipient_name},\n\nPO <b>{po_number}</b> was expected to be complete on "
        "<b>{completion_date}</b> — {days_overdue} day(s) ago — and <b>{requester_name}</b> "
        "has not yet confirmed the service or created a goods receipt.\n\n"
        "Until it is confirmed the vendor cannot be paid.\n\n"
        "<b>Vendor:</b> {vendor}\n\n"
        "<a href=\"{link}\">Review PO</a>\n\n{company_name}",
    ),
    # ── PA ────────────────────────────────────────────────────────────────────
    "create_pa_reminder": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Create Payment Application for {gr_number}",
        "Hi {recipient_name},\n\nInvoice <b>{invoice_number}</b> has been received and GR <b>{gr_number}</b> is complete.\n"
        "Please create a Payment Application to proceed with vendor payment.\n\n"
        "<b>Vendor:</b> {vendor}\n<b>Invoice Amount:</b> CAD {amount}\n\n"
        "<a href=\"{link}\">Create Payment Application</a>\n\n{company_name}",
    ),
    "confirm_receipt": _DEFAULT_EMAIL_TEMPLATE(
        "Invoice received for {po_number} — please confirm goods receipt",
        "Hi {recipient_name},\n\nInvoice <b>{invoice_number}</b> from {vendor} has been matched to "
        "PO <b>{po_number}</b>, but the goods/service has not been received yet. "
        "Please confirm receipt and create a Goods Receipt.\n\n"
        "<a href=\"{link}\">Create Goods Receipt</a>\n\n{company_name}",
    ),
    "pa_approval_request": _DEFAULT_EMAIL_TEMPLATE(
        "Action Required: Approve PA {pa_number}",
        "Hi {recipient_name},\n\nPayment Application <b>{pa_number}</b> requires your approval.\n\n"
        "<b>Amount:</b> CAD {amount}\n\n<a href=\"{link}\">Review &amp; Approve PA</a>\n\n{company_name}",
    ),
    "pa_approved": _DEFAULT_EMAIL_TEMPLATE(
        "Your PA {pa_number} has been approved",
        "Hi {recipient_name},\n\nPayment Application <b>{pa_number}</b> has been approved.\n\n"
        "<a href=\"{link}\">View PA</a>\n\n{company_name}",
    ),
    "prepayment_settlement_overdue": _DEFAULT_EMAIL_TEMPLATE(
        "Settlement Overdue — PA {pa_number}",
        "Hi {recipient_name},\n\nPrepayment settlement for PA <b>{pa_number}</b> is overdue. Please submit a Settlement PA as soon as possible.\n\n"
        "<a href=\"{link}\">View PA</a>\n\n{company_name}",
    ),
    # ── Reminders ─────────────────────────────────────────────────────────────
    "daily_pending_reminder": _DEFAULT_EMAIL_TEMPLATE(
        "Reminder: {document_number} is still awaiting your action",
        "Hi {recipient_name},\n\n<b>{document_number}</b> is still pending and requires your attention.\n\n"
        "<a href=\"{link}\">Go to Task</a>\n\n{company_name}",
    ),
    "sla_escalation": _DEFAULT_EMAIL_TEMPLATE(
        "SLA Escalation: {document_type} {document_number}",
        "Hi {recipient_name},\n\n{document_type} <b>{document_number}</b> has been waiting and requires urgent attention.\n\n"
        "<a href=\"{link}\">View Document</a>\n\n{company_name}",
    ),
    # ── Invoice match ──────────────────────────────────────────────────────────
    "match_invoice_assigned": _DEFAULT_EMAIL_TEMPLATE(
        "Invoice {invoice_number} assigned to you for PO matching",
        "Hi {recipient_name},\n\nYou have been assigned to match invoice <b>{invoice_number}</b> "
        "from {vendor} (CAD {amount}) to its purchase order(s).\n\n"
        "<a href=\"{link}\">Open Invoice &amp; Match to PO</a>\n\n{company_name}",
    ),
    "match_review_request": _DEFAULT_EMAIL_TEMPLATE(
        "Confirm invoice match — {invoice_number}",
        "Hi {recipient_name},\n\nA delegate has matched invoice <b>{invoice_number}</b> "
        "on your behalf. Please confirm it is linked to the correct purchase order and "
        "goods receipt.\n\nThis is a confirmation of the linkage only — approval of the "
        "payment amount happens later, on the Payment Application approval chain.\n\n"
        "<a href=\"{link}\">Confirm Match</a>\n\n{company_name}",
    ),
    "exception_resolution_request": _DEFAULT_EMAIL_TEMPLATE(
        "Invoice {invoice_number} is outside match tolerance",
        "Hi {recipient_name},\n\nInvoice <b>{invoice_number}</b> from {vendor} "
        "(CAD {amount}) could not be matched within tolerance. Please review the "
        "allocation and either resolve the exception or return the invoice.\n\n"
        "<a href=\"{link}\">Open Invoice</a>\n\n{company_name}",
    ),
}

# Previous default text for templates whose *default* copy was rewritten after
# already shipping (as opposed to a brand-new key, which the plain "missing
# keys" backfill below already handles). Used only as a comparison target in
# get_or_create: a stored template that still matches this old default verbatim
# was never customised, so it's safe to upgrade in place. A stored template
# that differs was edited by a customer and must never be overwritten. Do not
# reuse this dict for anything else — it exists solely for that one comparison.
_SUPERSEDED_EMAIL_TEMPLATE_DEFAULTS: dict = {
    # Reworded 2026-08-13 (63b3e0d): the old copy read as "approve or reject a
    # payment", which is what caused AP to refuse the task. See
    # match_review_request above for the current default.
    "match_review_request": _DEFAULT_EMAIL_TEMPLATE(
        "Match review required — invoice {invoice_number}",
        "Hi {recipient_name},\n\nThe assigned matcher has completed matching on invoice <b>{invoice_number}</b> "
        "with a non-zero variance. Please review the allocation and approve or reject it.\n\n"
        "<a href=\"{link}\">Review Match</a>\n\n{company_name}",
    ),
}

_P = lambda **kw: {k: kw.get(k, False) for k in PERMISSION_KEYS}  # noqa: E731

# Defaults follow PRD §1.3 visibility table. view_* default True means the role
# can call the endpoint; per-role scope (own / own dept / mapped / all) still
# applies via app/core/access_scope.py.
_VIEW_ALL = dict(view_pr=True, view_po=True, view_gr=True, view_invoice=True, view_pa=True)

# Portal Finance visibility shorthands (see PERMISSION_KEYS note above).
# _BUDGET_VIEW = the two cross-module budget views; _FINANCE_ALL = budget views
# plus the rest of the FINANCE section.
_BUDGET_VIEW = dict(view_budget_dashboard=True, view_budget_plans=True)
_FINANCE_ALL = dict(view_budget_dashboard=True, view_budget_plans=True, view_finance=True)

# All roles can book meeting rooms by default; manage_meeting_rooms stays
# False for everyone except system_admin (granted via Access Control Matrix UI).
_BOOKING = dict(view_booking=True)

_DEFAULT_ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    "requester":            _P(create_pr=True,  create_gr=True,  **_VIEW_ALL, **_BOOKING),
    "dept_admin":           _P(create_pr=True,  create_gr=True,  **_VIEW_ALL, **_BOOKING),
    "dept_manager":         _P(create_pr=True,  create_gr=True,  **_VIEW_ALL, **_BUDGET_VIEW, **_BOOKING),
    "supervisor":           _P(view_pr=True, **_BOOKING),
    "director":             _P(view_pr=True, view_pa=True, **_BOOKING),
    "gm":                   _P(create_pr=True,  create_gr=True,  **_VIEW_ALL, **_BOOKING),
    "opm":                  _P(create_pr=True,  create_gr=True,  **_VIEW_ALL, **_BOOKING),
    "procurement_officer":  _P(create_gr=True,  vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager":  _P(create_gr=True,  vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "warehouse_staff":      _P(create_gr=True,  view_gr=True, **_BOOKING),
    "ap_clerk":             _P(create_gr=True,  invoice_upload=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_bp":           _P(create_gr=True,  pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":      _P(create_pr=True,  create_gr=True,  admin_panel=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "vendor_manager":       _P(vendor_master=True, admin_panel=True, **_BOOKING),
    "cfo":                  _P(pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "auditor":              _P(**_VIEW_ALL, **_BOOKING),
    "erp_pa_officer":       _P(**_VIEW_ALL, **_BOOKING),
    # NOTE: deliberately NOT _VIEW_ALL. This set must match identity's 0008
    # migration _GRANTS exactly (view_po / view_invoice / view_pa /
    # view_finance), or "what the migration seeds" and "what the matrix
    # default claims" disagree forever. Narrower than erp_pa_officer's
    # _VIEW_ALL on purpose: payment_officer exists to SEGREGATE duties and
    # acts on already-approved PAs — receipt and requisition were verified
    # upstream, so view_pr / view_gr are not needed. view_finance IS needed
    # (2026-08-13 whole-phase-review fix): without it Portal's finance nav
    # (navConfig.tsx) hides Payments Hub / Payment Batches / Remittance, and
    # batch payment is core to the role even though finance-api's own gate
    # already permits it.
    "payment_officer":      _P(view_po=True, view_invoice=True, view_pa=True, view_finance=True, **_BOOKING),
    "system_admin":         {k: True for k in PERMISSION_KEYS},
}

_DEFAULT_WORKFLOW_DEFS = {
    "pr": [
        {"id": "supervisor",   "role": "supervisor",   "label": "Supervisor"},
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "director",     "role": "director",     "label": "Director"},
        {"id": "gm_or_opm",    "role": "gm_or_opm",    "label": "GM / OPM"},
    ],
    "po": [
        {"id": "po-step-0", "label": "Procurement Manager", "role": "procurement_manager"},
        {"id": "po-step-1", "label": "Finance Manager",     "role": "finance_manager"},
    ],
    "pa": [
        {"id": "dept_manager", "role": "dept_manager",   "label": "Department Manager"},
        {"id": "director",     "role": "director",       "label": "Director"},
        {"id": "gm_or_opm",    "role": "gm_or_opm",      "label": "GM / OPM"},
        {"id": "finance_bp",   "role": "finance_bp",     "label": "Finance BP"},
        {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
    ],
}


def _build_default() -> CompanyConfig:
    return CompanyConfig(
        name="EPMS",
        tagline="Enterprise Procurement Management",
        delivery_address="",
        default_currency="CAD",
        enabled_currencies=["CAD", "USD", "EUR", "CNY"],
        custom_currencies=[],
        mfa_enabled=True,
        password_expiry_days=90,
        po_email_subject="Purchase Order {po_number} from {company_name}",
        po_email_body="Dear {vendor_name},\n\nPlease find attached Purchase Order {po_number}.\n\nThank you.",
        pdf_templates=_DEFAULT_PDF_TEMPLATES,
        workflow_config=_DEFAULT_WORKFLOW_CONFIG,
        dept_gm_opm_mapping={},
        dept_supervisor_enabled={},
        dept_director_mapping={},
        service_gr_sla=_DEFAULT_SERVICE_GR_SLA,
        gr_notification_sla=_DEFAULT_GR_NOTIFICATION_SLA,
        prepayment_config=_DEFAULT_PREPAYMENT_CONFIG,
        budget_admin_config=_DEFAULT_BUDGET_ADMIN_CONFIG,
        collection_config=_DEFAULT_COLLECTION_CONFIG,
        role_management={
            "gm_user_id": None, "gm_backup_user_id": None,
            "opm_user_id": None, "opm_backup_user_id": None,
            "finance_bp_user_ids": [],
        },
        workflow_defs=_DEFAULT_WORKFLOW_DEFS,
        role_permissions=_DEFAULT_ROLE_PERMISSIONS,
        custom_roles=[],
        email_templates=_DEFAULT_EMAIL_TEMPLATES,
        notification_settings=_DEFAULT_NOTIFICATION_SETTINGS,
    )


# ── Config CRUD ──────────────────────────────────────────────────────────────

async def get_or_create(db: AsyncSession) -> CompanyConfig:
    """Return the singleton config row, creating it with defaults if absent.

    Also backfills any email_templates keys that are missing from the defaults
    (so newly added templates are available without a full config reset), and
    upgrades any stored template that still matches a *previous* default
    verbatim to the current default (so a copy rewrite ships to already-seeded
    DBs). A stored template whose content differs from the previous default —
    i.e. a customer edited it — is never touched.
    """
    result = await db.execute(select(CompanyConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = _build_default()
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)
    else:
        existing = cfg.email_templates or {}
        # Backfill any missing email template keys from defaults
        missing = {k: v for k, v in _DEFAULT_EMAIL_TEMPLATES.items() if k not in existing}
        # Upgrade stored templates that still equal a since-superseded default
        upgrades = {
            k: _DEFAULT_EMAIL_TEMPLATES[k]
            for k, old_default in _SUPERSEDED_EMAIL_TEMPLATE_DEFAULTS.items()
            if k in existing and existing[k] == old_default
        }
        if missing or upgrades:
            cfg.email_templates = {**existing, **missing, **upgrades}
            flag_modified(cfg, "email_templates")
            await db.flush()
    return cfg


_JSONB_FIELDS = frozenset({
    "enabled_currencies", "custom_currencies", "pdf_templates", "workflow_config",
    "dept_gm_opm_mapping", "dept_supervisor_enabled", "dept_director_mapping", "service_gr_sla",
    "gr_notification_sla", "prepayment_config", "budget_admin_config",
    "collection_config", "remittance_config", "role_management", "workflow_defs",
    "role_permissions", "custom_roles", "email_templates", "notification_settings",
    "vendor_categories",
})


async def update(
    db: AsyncSession, cfg: CompanyConfig, payload: ConfigUpdate, actor_id: uuid.UUID
) -> CompanyConfig:
    fields = payload.model_dump(exclude_none=True)
    for field, value in fields.items():
        if field == "notification_settings" and isinstance(value, dict):
            # 浅合并:Portal 与 EPMS 两个客户端 PATCH 同一个 /config,各自只提交
            # 自己那几个键。整块替换会让 Portal 保存通知表单时把 EPMS 写的
            # role_shared_mailboxes / system_url 静默抹掉。
            # 故意只做一层浅合并 —— 客户端提交完整的 role_shared_mailboxes 子字典
            # 时仍然整体替换该子字典,这样 EPMS UI 里删除某个角色的映射依旧生效。
            value = {**(cfg.notification_settings or {}), **value}
        setattr(cfg, field, value)
        if field in _JSONB_FIELDS:
            flag_modified(cfg, field)
    cfg.updated_by = actor_id
    await db.flush()
    await db.refresh(cfg)
    return cfg


# ── Role Permissions CRUD ────────────────────────────────────────────────────

def get_effective_role_permissions(cfg: CompanyConfig) -> dict[str, dict[str, bool]]:
    """Merge stored permissions with defaults; missing keys fall back to defaults."""
    result: dict[str, dict[str, bool]] = {}
    # Built-in roles: start from defaults, overlay stored overrides
    for role, default_perms in _DEFAULT_ROLE_PERMISSIONS.items():
        stored = cfg.role_permissions.get(role, {})
        merged = {k: stored.get(k, default_perms[k]) for k in PERMISSION_KEYS}
        # Enforce locked permissions
        for perm in LOCKED_PERMISSIONS.get(role, set()):
            merged[perm] = True
        result[role] = merged
    # Custom roles
    for cr in cfg.custom_roles:
        code = cr["code"]
        if not cr.get("is_active", True):
            continue
        stored = cfg.role_permissions.get(code, {})
        result[code] = {k: bool(stored.get(k, False)) for k in PERMISSION_KEYS}
    return result


async def update_role_permissions(
    db: AsyncSession,
    cfg: CompanyConfig,
    payload: RolePermissionsUpdate,
    actor_id: uuid.UUID,
) -> CompanyConfig:
    stored: dict = dict(cfg.role_permissions)
    custom_codes = {cr["code"] for cr in cfg.custom_roles}
    all_valid = BUILT_IN_ROLES | custom_codes

    for role, perms in payload.permissions.items():
        if role not in all_valid:
            raise HTTPException(status_code=422, detail=f"Unknown role: '{role}'")
        locked = LOCKED_PERMISSIONS.get(role, set())
        role_stored = dict(stored.get(role, {}))
        for perm_key, value in perms.items():
            if perm_key not in PERMISSION_KEYS:
                raise HTTPException(status_code=422, detail=f"Unknown permission: '{perm_key}'")
            if perm_key in locked and not value:
                raise HTTPException(
                    status_code=422,
                    detail=f"Permission '{perm_key}' is locked for role '{role}' and cannot be disabled",
                )
            role_stored[perm_key] = value
        stored[role] = role_stored

    cfg.role_permissions = stored
    cfg.updated_by = actor_id
    flag_modified(cfg, "role_permissions")
    await db.flush()
    await db.refresh(cfg)
    return cfg


# ── Custom Roles CRUD ────────────────────────────────────────────────────────

def list_all_roles(cfg: CompanyConfig) -> list[dict]:
    """Return built-in roles + custom roles as a flat list."""
    built_in = [
        {
            "code": code,
            "name": _BUILTIN_ROLE_NAMES.get(code, code),
            "description": "",
            "is_active": True,
            "is_builtin": True,
            "assignable_as_primary": code not in ADDITIONAL_ONLY_ROLES,
        }
        for code in BUILT_IN_ROLES
    ]
    custom = [
        {"assignable_as_primary": True, **cr, "is_builtin": False}
        for cr in cfg.custom_roles
    ]
    return built_in + custom


async def create_custom_role(
    db: AsyncSession,
    cfg: CompanyConfig,
    payload: CustomRoleCreate,
) -> dict:
    code = payload.code
    if code in BUILT_IN_ROLES:
        raise HTTPException(status_code=409, detail=f"Role code '{code}' is reserved for a built-in role")
    existing_codes = {cr["code"] for cr in cfg.custom_roles}
    if code in existing_codes:
        raise HTTPException(status_code=409, detail=f"Role code '{code}' already exists")
    new_role = {
        "code": code,
        "name": payload.name,
        "description": payload.description,
        "is_active": True,
    }
    cfg.custom_roles = [*cfg.custom_roles, new_role]
    flag_modified(cfg, "custom_roles")
    await db.flush()
    await db.refresh(cfg)
    return {**new_role, "is_builtin": False}


async def update_custom_role(
    db: AsyncSession,
    cfg: CompanyConfig,
    role_code: str,
    payload: CustomRoleUpdate,
) -> dict:
    if role_code in BUILT_IN_ROLES:
        raise HTTPException(status_code=403, detail="Built-in roles cannot be modified here")
    roles = list(cfg.custom_roles)
    idx = next((i for i, r in enumerate(roles) if r["code"] == role_code), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Custom role '{role_code}' not found")
    role = dict(roles[idx])
    if payload.name is not None:
        role["name"] = payload.name
    if payload.description is not None:
        role["description"] = payload.description
    if payload.is_active is not None:
        role["is_active"] = payload.is_active
    roles[idx] = role
    cfg.custom_roles = roles
    flag_modified(cfg, "custom_roles")
    await db.flush()
    await db.refresh(cfg)
    return {**role, "is_builtin": False}


async def delete_custom_role(
    db: AsyncSession,
    cfg: CompanyConfig,
    role_code: str,
) -> None:
    if role_code in BUILT_IN_ROLES:
        raise HTTPException(status_code=403, detail="Built-in roles cannot be deleted")
    roles = list(cfg.custom_roles)
    new_roles = [r for r in roles if r["code"] != role_code]
    if len(new_roles) == len(roles):
        raise HTTPException(status_code=404, detail=f"Custom role '{role_code}' not found")
    cfg.custom_roles = new_roles
    flag_modified(cfg, "custom_roles")
    # Also remove any stored permissions for this role
    perms = dict(cfg.role_permissions)
    perms.pop(role_code, None)
    cfg.role_permissions = perms
    flag_modified(cfg, "role_permissions")
    await db.flush()
    await db.refresh(cfg)


# Display names for built-in roles (used in list_all_roles)
_BUILTIN_ROLE_NAMES: dict[str, str] = {
    "requester": "Requester",
    "dept_admin": "Department Admin",
    "dept_manager": "Department Manager",
    "gm": "General Manager",
    "opm": "Operations Manager",
    "procurement_officer": "Procurement Officer",
    "procurement_manager": "Procurement Manager",
    "warehouse_staff": "Warehouse Staff",
    "ap_clerk": "AP Clerk",
    "finance_bp": "Finance Business Partner",
    "finance_manager": "Finance Manager",
    "vendor_manager": "Vendor Manager",
    "cfo": "CFO",
    "auditor": "Auditor",
    "erp_pa_officer": "ERP PA Officer",
    "payment_officer": "Payment Officer",
    "system_admin": "System Admin",
}


def role_display_name(cfg: CompanyConfig, role_code: str | None) -> str:
    """Human-readable name for a role code (built-in, custom, or unknown)."""
    if not role_code:
        return "Team"
    if role_code in _BUILTIN_ROLE_NAMES:
        return _BUILTIN_ROLE_NAMES[role_code]
    for cr in (cfg.custom_roles or []):
        if cr.get("code") == role_code:
            if not cr.get("is_active", True):
                break
            return cr.get("name") or role_code
    return role_code.replace("_", " ").title()
