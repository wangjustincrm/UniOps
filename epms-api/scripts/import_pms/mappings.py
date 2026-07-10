"""Static crosswalks and enum mappings for the PMS → EPMS migration.

Sources of truth provided by the business:
  * PR Type mapping            — user-confirmed (see PR_TYPE_MAP)
  * Cost Center crosswalk      — image.png (SharePoint identifier → EPMS code)
  * Applier → EPMS user        — user.txt (SharePoint UserRole name → EPMS email)
"""
from __future__ import annotations

import re

# ── SharePoint lists we extract, and the columns we keep ────────────────────────
# Header lists (current + Backup share the same schema)
PR_FIELDS = [
    "ID", "Title", "PR_x0020_No", "PONo", "Vendor", "Department", "CostCenter",
    "GLCode", "TotalPrice", "Status", "Currency", "Applier", "PRType",
    "ProjectNo", "Created", "Modified",
]
PO_FIELDS = [
    "ID", "Title", "Supplier", "Currency", "TotalPrice", "Status",
    "PaymentStatus", "ReceiveStatus", "Status0", "PayFirst", "Freight",
    "Comment", "Created", "Modified",
]
PA_FIELDS = [
    "ID", "Title", "Supplier", "PONO", "Currency", "TotalPrice", "ItemsTotal",
    "Tax", "FreightFee", "OtherFee", "Status", "Status0", "Applier",
    "Department", "DueDate", "InvoiceIssueDate", "Comment", "Created", "Modified",
    # HoldBy: single-line-of-text naming the AP clerk who handled the PA in the
    # legacy PMS. Used to reconstruct the ap_clerk approval event (see reconstruct.py).
    "HoldBy",
]
# Line-item lists (each holds the full history — no Backup variant)
PR_ITEM_FIELDS = [
    "ID", "Title", "CRMPartNo", "Description", "PartNo", "UOM", "Qty",
    "UnitPrice", "Total_x0020_Price", "Created", "Modified",
]
PO_ITEM_FIELDS = [
    "ID", "Title", "Description", "QTY", "UOM", "UnitPrice", "TotalPrice",
    "PRITEMID", "PartNo", "InvoiceID", "ReceivedQTY", "ReceiveStatus",
    "PaymentStatus", "ReceivedDate", "Created", "Modified",
]
PA_ITEM_FIELDS = [
    "ID", "Title", "Description", "QTY", "UOM", "UnitPrice", "TotalPrice",
    "POITEMID", "InvoiceID", "PartNo", "Department", "CostCenter", "GLCode",
    "Created", "Modified",
]
INVOICE_FIELDS = ["ID", "Title", "PONo", "IssueDate", "Created", "Modified"]
VENDORLIST_FIELDS = [
    "ID", "Title", "Contractor", "EmailAddress", "Phone", "Address", "POID",
    "NetTerm_x0028_Days_x0029_",
]

# list_title -> (output filename, select-fields). The loader reads by filename.
EXTRACT_SPEC: dict[str, tuple[str, list[str]]] = {
    "Purchase Request":        ("pr.json", PR_FIELDS),
    "Purchase Request Backup": ("pr_backup.json", PR_FIELDS),
    "PR Item":                 ("pr_item.json", PR_ITEM_FIELDS),
    "PO List":                 ("po.json", PO_FIELDS),
    "PO List Backup":          ("po_backup.json", PO_FIELDS),
    "PO Item":                 ("po_item.json", PO_ITEM_FIELDS),
    "Payment Request":         ("pa.json", PA_FIELDS),
    "Payment Request Backup":  ("pa_backup.json", PA_FIELDS),
    "Payment Item":            ("pa_item.json", PA_ITEM_FIELDS),
    "INVOICE":                 ("invoice.json", INVOICE_FIELDS),
    "vendorlist":              ("vendorlist.json", VENDORLIST_FIELDS),
}

# ── PR Type → EPMS type int (1=Raw Materials,2=Consumables,3=Spare Parts, ──────
#    4=Service,5=Fixed Assets,6=Software). Business-confirmed mapping: ──────────
PR_TYPE_MAP: dict[str, int] = {
    "Parts": 2,
    "Services": 4,
    "Inventory Restock": 3,
    "Fixed Asset": 5,
    "Project": 6,
}
PR_TYPE_DEFAULT = 4  # unknown → Service

# ── Cost Center crosswalk (from image.png) ──────────────────────────────────────
# Key = SharePoint "identify" = Department + CostCenter concatenated (no separator),
# e.g. PR.Department="Engineering" + PR.CostCenter="E04 - Maintenance".
COST_CENTER_XWALK: dict[str, str] = {
    "ProductionP02 - Process/Pretreatment": "MOH-0104-P02",
    "ProductionP03 - Packaging/Dry Mixing": "MOH-0104-P03",
    "ProductionP01 -General": "MOH-0104-P01",
    "Administration & HRNormal": "GA-0101",
    "Administration & HRHSE": "MOH-0101",
    "Office of General ManagerNormal": "GA-0100",
    "FinanceNormal": "GA-0103",
    "EngineeringNormal": "MOH-0106-E01",
    "EngineeringE06 - WWTP": "MOH-0106-E06",
    "EngineeringE05 - TechSupport": "MOH-0106-E04",
    "EngineeringE02 - Utility": "MOH-0106-E02",
    "EngineeringE01 - General": "MOH-0106-E01",
    "EngineeringE04 - Maintenance": "MOH-0106-E04",
    "Quality AssuranceLAB": "MOH-0105-LAB",
    "R&DNormal": "RD-0109",
    "MarketingNormal": "SELL-0111",
    "Business DevelopmentNormal": "SELL-0112",
    "E-CommerceNormal": "SELL-0113",
    "Supply Chain & LogisticsNormal": "GA-0107",
    # ── User-provided rules for previously-unmapped combos (2026-06-09) ──────────
    "Quality AssuranceNormal": "GA-0105",
    "Supply Chain & LogisticsS02 - Overheads": "MOH-0107-S02",
    "EngineeringNONE": "MOH-0106-E01",
    "Supply Chain & LogisticsS02 - Overheads and Warehouse": "MOH-0107-S02",
    "EngineeringProject": "MOH-0106-E01",
    "ProductionP01 - Admin": "MOH-0104-P01",
    "EngineeringE01 - Admin": "MOH-0106-E01",
    "Supply Chain & LogisticsS01 - ADMIN": "GA-0107",
    "Quality AssuranceNONE": "GA-0105",
    "Supply Chain & LogisticsNONE": "GA-0107",
    "ProductionNormal": "MOH-0104-P01",
    "SalesNormal": "SELL-0110",
    "EngineeringMaintenance": "MOH-0106-E04",
    "Supply Chain & LogisticsCRM010-SUPPLIES AND TOOLS": "GA-0107",
}
# Fuzzy index: drop ALL whitespace + lowercase, so dash-spacing variants
# ("E01 - General" vs "E01-General") still match.
def _cc_norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


_COST_CENTER_XWALK_NORM = {_cc_norm(k): v for k, v in COST_CENTER_XWALK.items()}


def resolve_cc_code(department: str | None, cost_center: str | None) -> str | None:
    """Map a PMS (Department, CostCenter) pair to an EPMS cost-center code."""
    dept = (department or "").strip()
    # Engineering cost centers were consolidated in EPMS — only MOH-0106-E01
    # remains (E02/E04/E06 deleted), so all Engineering combos map to it.
    if dept.lower() == "engineering":
        return "MOH-0106-E01"
    ident = f"{dept}{(cost_center or '').strip()}"
    if ident in COST_CENTER_XWALK:
        return COST_CENTER_XWALK[ident]
    return _COST_CENTER_XWALK_NORM.get(_cc_norm(ident))


# ── Vendor POID (the code embedded in PO numbers; equals EPMS vendor.code) ───────
_POID_RE = re.compile(r"^[A-Za-z]+-(\d+)-")


def poid_from_po_number(po_number: str | None) -> str | None:
    """'PO-553-2606-08' → '553'  (the vendor POID == EPMS vendor.code)."""
    if not po_number:
        return None
    m = _POID_RE.match(po_number.strip())
    return m.group(1) if m else None


def poid_code_candidates(poid) -> list[str]:
    """EPMS codes are zero-padded to 3 ('081'); PO tokens may or may not be."""
    try:
        n = int(poid)
    except (TypeError, ValueError):
        return []
    raw = str(n)
    return list(dict.fromkeys([raw, raw.zfill(3)]))


# ── Applier → EPMS user email (from user.txt) ───────────────────────────────────
USER_XWALK: dict[str, str] = {
    "JustinW": "wangjustin@canadaroyalmilk.com",
    "Chenggang": "hanchenggang@canadaroyalmilk.com",
    "Farshid": "mohammadi@canadaroyalmilk.com",
    "Sue": "zhengsue@canadaroyalmilk.com",
    "Diana": "mojica@canadaroyalmilk.com",
    "Etienne": "clement@canadaroyalmilk.com",
    "Brian": "gordon@canadaroyalmilk.com",
    "Miguel": "morales@canadaroyalmilk.com",
    "Lihua": "xincynthia@canadaroyalmilk.com",
    "Bingxing": "zhaobingxing@canadaroyalmilk.com",
    "Cao": "caosteve@canadaroyalmilk.com",
    "Debbie": "carmelotes@canadaroyalmilk.com",
    "RJ": "noftall@canadaroyalmilk.com",
    "Mengqi": "zhangmengqi@canadaroyalmilk.com",
    "SUNQI": "sunqi@canadaroyalmilk.com",
    "Kirk": "smith@canadaroyalmilk.com",
    "Farisa": "hasan@canadaroyalmilk.com",
    "Les": "les@canadaroyalmilk.com",
    "Celia": "pao@canadaroyalmilk.com",
    "luyan": "luyan@canadaroyalmilk.com",
    "Andres": "pargamartin@canadaroyalmilk.com",
    "Sana": "shahsana@canadaroyalmilk.com",
    "Sivers": "sivers@canadaroyalmilk.com",
    "Darren": "leedw@canadaroyalmilk.com",
    "Kris": "enriquez@canadaroyalmilk.com",
    "Christine": "molloy@canadaroyalmilk.com",
    "FarshidENG": "mohammadi@canadaroyalmilk.com",
    "Prithvi": "subramanian@canadaroyalmilk.com",
    "Khoa": "truong@canadaroyalmilk.com",
    "Victoria": "javela@canadaroyalmilk.com",
    "Mark": "santangini@canadaroyalmilk.com",
    "Yuhong": "liuyuhong@canadaroyalmilk.com",
    "Javela": "javela@canadaroyalmilk.com",
    "Boyan": "weiboyan@canadaroyalmilk.com",
    "Raghul": "balakrishnan@canadaroyalmilk.com",
    "Mahira": "bintereza@canadaroyalmilk.com",
    "Limz": "leemt@canadaroyalmilk.com",
    "Huangyp": "huangyp@canadaroyalmilk.com",
    "Blair": "hwang@canadaroyalmilk.com",
    "Cindy": "karlovic@canadaroyalmilk.com",
    "Raghav": "monga@canadaroyalmilk.com",
    "Jason": "cameron@canadaroyalmilk.com",
    "ElhamY": "yousefinejad@canadaroyalmilk.com",
    "Kody": "wangyide@canadaroyalmilk.com",
    "baiyi": "liubaiyi@canadaroyalmilk.com",
    "Forough": "salehi@canadaroyalmilk.com",
    "jenil": "pateljenil@canadaroyalmilk.com",
    "cox": "cox@canadaroyalmilk.com",
    "JustinZhou": "zhoujustin@canadaroyalmilk.com",
    "Lynn Zhu": "zhulynn@canadaroyalmilk.com",
    "Lynn": "zhulynn@canadaroyalmilk.com",   # same person as Lynn Zhu
}
# Aliases: SharePoint Applier variants that are clearly the same person as a
# canonical UserRole name above (department suffixes, full names, last names).
USER_ALIASES: dict[str, str] = {
    "Justin Wang": "JustinW",
    "Elham": "ElhamY",
    "ElhamENG": "ElhamY",
    "FarshidPRD": "Farshid",
    "FarshidPRO": "Farshid",
    "Boyan Wei": "Boyan",
    "Parga": "Andres",
    "Morales": "Miguel",
}

# Case-insensitive index (canonical + aliases).
_USER_XWALK_CI = {k.lower(): v for k, v in USER_XWALK.items()}
for _alias, _canon in USER_ALIASES.items():
    if _canon in USER_XWALK:
        _USER_XWALK_CI[_alias.lower()] = USER_XWALK[_canon]


def resolve_user_email(applier: str | None) -> str | None:
    if not applier:
        return None
    return _USER_XWALK_CI.get(applier.strip().lower())


# ── Status maps ────────────────────────────────────────────────────────────────
# EPMS PR statuses:  draft|submitted|in_review|approved|returned|rejected|cancelled|issued
def map_pr_status(status: str | None, has_po: bool = False) -> str:
    # SharePoint "Approved" → EPMS "approved" (the PR list has no 'issued' filter,
    # so 'issued' would render as the In-Review fallback badge).
    s = (status or "").strip().upper()
    if "REJECT" in s and "EDIT" in s:   # "Rejected To Edit" → sent back to edit
        return "returned"
    if "REJECT" in s:
        return "rejected"
    if "APPROVING" in s or "REVIEW" in s:
        return "in_review"
    if "APPROVED" in s or "COMPLETE" in s:
        return "approved"
    return "approved" if not s else "submitted"


# EPMS PO statuses: draft|submitted|in_review|approved|returned|rejected|issued|
#                    partially_received|fully_received|closed|cancelled
def map_po_status(status: str | None, final: str | None, receive: str | None = None,
                  payment: str | None = None) -> str:
    """Map SharePoint PO approval status (`Status`), final status (`Status0`),
    receive status (`ReceiveStatus`) and payment status (`PaymentStatus`)
    → EPMS status. Precedence matters:
      * REJECTED → cancelled — checked FIRST because rejected PMS POs carry
        Status0='CLOSED', which would otherwise mis-map them to 'closed'.
      * Status0 CANCELED → cancelled (also catches POs cancelled mid-approval).
      * PaymentStatus PAID → closed — 用户决策(2026-07-10):付款是最高业务信号,
        老系统大量已付清 PO 的 Status0 仍是 OPEN,若不按 PAID 收口会以 issued
        进入 EPMS,灌爆发票匹配的候选列表。
      * Status0 COMPLETED/CLOSED → closed (business-completed; user decision:
        takes priority over receive status).
      * Approval still running (…APPROVING) → in_review.
      * GM APPROVED → refine by delivery: PLACING ORDER→approved (awaiting the
        purchasing office to place it), RECEIVED→fully_received, else→issued.
    """
    s = (status or "").strip().upper()
    f = (final or "").strip().upper()
    r = (receive or "").strip().upper()
    p = (payment or "").strip().upper()
    if "REJECT" in s:
        return "cancelled"
    if f in ("CANCELED", "CANCELLED"):
        return "cancelled"
    if p == "PAID":
        return "closed"
    if f in ("COMPLETED", "CLOSED", "CLOSE"):
        return "closed"
    if "APPROVING" in s:
        return "in_review"
    if "APPROVED" in s:
        if "PLACING ORDER" in r:
            return "approved"
        if "RECEIVED" in r:        # note: "NO RECEIVE" lacks the trailing D
            return "fully_received"
        return "issued"           # DELIVERING / DELIVERYING / NO RECEIVE / blank
    return "submitted"


def po_approval_step_idx(status: str | None) -> int:
    """Which PO workflow step (0-based) the approval sits at, for the Approval
    Timeline. PO workflow = [Procurement Manager(0), GM/OPM(1)].
      SC MANAGER APPROVING → step 0 current (0 done)
      GM/OPM APPROVING     → step 1 current (Procurement Manager done)
      GM APPROVED          → both steps done (2)
    """
    s = (status or "").strip().upper()
    if "APPROVED" in s:
        return 2
    if "GM APPROVING" in s or "OPM APPROVING" in s:
        return 1
    return 0


# EPMS PA statuses: draft|submitted|in_review|approved|processed|cancelled
def map_pa_status(status: str | None, final: str | None) -> str:
    f = (final or "").strip().upper()
    if f in ("CANCELED", "CANCELLED"):
        return "cancelled"
    s = (status or "").strip().upper()
    if "REJECT" in s:
        return "cancelled"
    if "PAID" in s:
        return "processed"
    if "WAITING PAYMENT" in s:
        return "approved"
    if "APPROVING" in s or "REVIEW" in s:
        return "in_review"
    return "submitted"


def pa_approval_step_idx(status: str | None) -> int:
    """Which PA workflow step (0-based) the approval sits at, for the Approval
    Timeline. PA workflow = [Department Manager(0), GM/OPM(1), Finance Director of
    Oversea Dept / finance_bp(2), AP Review / ap_clerk(3), Finance Manager(4)].
    5 = all five approvals done (WAITING PAYMENT → awaiting Payment Processed; PAID
    → fully processed)."""
    s = (status or "").strip().upper()
    if "PAID" in s or "WAITING PAYMENT" in s:
        return 5
    if "DEP MANAGER APPROVING" in s:
        return 0
    if "GM APPROVING" in s or "OPM APPROVING" in s:
        return 1
    if "BP APPROVING" in s:
        return 2
    if "AP REVIEW" in s:
        return 3
    if "FN MANAGER APPROVING" in s:
        return 4
    return 0


def normalize_currency(cur: str | None) -> str:
    c = (cur or "CAD").strip().upper()
    return "CNY" if c in ("RMB", "CNY", "YMB") else (c or "CAD")


# PR numbers to skip entirely (business-flagged junk/rejected records).
PR_SKIP: set[str] = {
    "PR-20251205-0003",  # ACS Valves
    "PR-20240802-0005",  # SHENYANG KANOMAX
    "PR-20241003-0003",  # Sludgemapper
    "PR-20250605-0001",  # JB Millwrighting services LTD
}


def payment_terms_from_days(days) -> str:
    try:
        n = int(float(days))
        return f"net{n}"
    except (TypeError, ValueError):
        return "net30"
