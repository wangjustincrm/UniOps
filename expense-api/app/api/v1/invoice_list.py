"""Unified invoice list — queries EPMS invoices + OA expense_invoices in one call.

Access control (PRD §INV-VIS):
  requester      → EPMS: own uploads OR invoices on their PO chain
                   OA:   own uploads (created_by = user_id)
  dept_manager   → EPMS: invoices on their department's PO chain
                   OA:   own uploads
  gm / opm       → EPMS: invoices on their mapped-department PO chain
                   OA:   own uploads
  all other roles → unrestricted
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUserDep, SessionDep
from app.models.epms_mirrors import (
    EpmsCostCenter,
    EpmsInvoice,
    EpmsPurchaseOrder,
    EpmsPurchaseRequest,
)
from app.models.invoice import ExpenseInvoice
from app.models.invoice_attachment import InvoiceAttachment

router = APIRouter(prefix="/invoices", tags=["invoices-unified"])


class UnifiedInvoice(BaseModel):
    id: str
    source: str                  # 'epms' | 'oa'
    invoice_number: Optional[str]
    vendor_name: Optional[str]
    total_amount: Decimal
    currency: str
    invoice_date: Optional[date]
    status: str
    file_name: Optional[str]
    created_at: datetime
    # EPMS-specific
    internal_ref: Optional[str] = None
    po_number: Optional[str] = None
    # OA-specific
    pa_number: Optional[str] = None
    # Attachment count
    attachment_count: int = 0


class UnifiedInvoiceList(BaseModel):
    items: list[UnifiedInvoice]
    total: int


# ── Scope helpers ─────────────────────────────────────────────────────────────

async def _epms_po_subq_for_dept(db: AsyncSession, dept_id: uuid.UUID):
    """Subquery: EPMS PO ids whose PR belongs to the given department."""
    cc_subq = select(EpmsCostCenter.id).where(EpmsCostCenter.department_id == dept_id)
    pr_subq = select(EpmsPurchaseRequest.id).where(
        EpmsPurchaseRequest.cost_center_id.in_(cc_subq)
    )
    return select(EpmsPurchaseOrder.id).where(EpmsPurchaseOrder.pr_id.in_(pr_subq))


async def _epms_po_subq_for_user(db: AsyncSession, user_id: uuid.UUID):
    """Subquery: EPMS PO ids whose PR was created by this user."""
    pr_subq = select(EpmsPurchaseRequest.id).where(EpmsPurchaseRequest.created_by == user_id)
    return select(EpmsPurchaseOrder.id).where(EpmsPurchaseOrder.pr_id.in_(pr_subq))


async def _user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """PRIMARY role + ADDITIONAL roles from identity's user_roles.

    Delegates to core.authz_matrix so there is ONE definition of "which roles
    does this user hold" in the service. The three local copies this replaced
    all read user_roles bare, without joining role_defs — so a role an admin
    had DEACTIVATED still granted OA approval rights and visibility to everyone
    holding it, while the matrix helper (used by invoice_attachments) correctly
    ignored it. Same table, two answers.
    """
    from app.core.authz_matrix import user_role_codes
    return await user_role_codes(db, user_id, base_role)


async def _caller_department_id(db: AsyncSession, user: dict) -> uuid.UUID | None:
    """The caller's own department, resolved from `users.department_id`.

    NOT from the JWT. `create_access_token` — epms-api's and identity-api's
    alike — emits `{sub, role, type}` and nothing else, so the
    `user["department_id"]` this used to read was always None and the
    dept_manager/dept_admin branch below never appended a single PO subquery:
    those roles silently fell through to own-uploads-only. Same failure as the
    blank Employee column (see test_expense_employee_name.py) — reading off the
    token what only the database has. epms-api resolves it from the column
    (app/core/access_scope.py) and this mirrors it.

    The token is still honoured if it ever starts carrying the claim, so adding
    it later is a no-op here rather than a conflict.
    """
    raw = user.get("department_id")
    if raw:
        return uuid.UUID(str(raw))
    row = (await db.execute(sa.text(
        "SELECT department_id FROM users WHERE id = :u"),
        {"u": str(user["sub"])})).scalar_one_or_none()
    return row


async def _gm_opm_dept_ids(db: AsyncSession, role: str) -> list[uuid.UUID]:
    """Department ids whose gm_or_opm routing resolves to `role` ('gm' | 'opm').

    Resolve mapped departments via approval-api's approval_dept_routing (same
    physical DB, read-only — expense-api never writes it; Portal → Approval
    Routing is the only writer). Single source of truth since the phase-3
    migration; company_config.dept_gm_opm_mapping is a frozen rollback snapshot
    (see epms-api/app/core/access_scope.py:_mapped_dept_ids, the reference impl).

    A department with NO approval_dept_routing row at all (e.g. one mdm-api just
    created) is treated as 'gm' via COALESCE, matching the approval engine's own
    fallback (`dept_gm_opm.get(str(dept_id), "gm")`), so a brand-new department's
    invoices — routed to GM for approval — stay visible to GM here.

    ⚠️ SIBLING COPY of epms-api/app/core/access_scope.py:_mapped_dept_ids — change
    both together (this codebase has been bitten by sibling copies drifting).
    不过滤 d.is_active —— 有意为之,与 epms 一致:停用部门的在途发票对 GM 必须仍可见
    (这里没有 task-chain 兜底,加过滤会让 GM 完全看不到)。
    """
    return list((await db.execute(sa.text(
        "SELECT d.id FROM departments d "
        "LEFT JOIN approval_dept_routing r ON r.dept_id = d.id "
        "WHERE COALESCE(r.gm_or_opm, 'gm') = :r"),
        {"r": role})).scalars().all())


async def _build_invoice_scope(db: AsyncSession, user: dict) -> dict:
    """Derive invoice visibility scope from the user's full role set.

    Multi-role users: visibility is the UNION of every role the user holds (JWT
    base role + ADDITIONAL roles in identity's user_roles). If ANY held role is
    unrestricted, the user is unrestricted; otherwise the EPMS PO scope is the
    OR (SQL UNION) of each restricted role's own scope. This is what lets a
    Department Manager who is ALSO a GM (hanchenggang) see both their own
    department's invoices and the invoices of the departments their GM role
    covers — before this the function branched on the single JWT base role and
    never even read user_roles, silently dropping the second role's scope.

    Returns:
      restrict (bool)       — whether any filter applies
      user_id (UUID)
      role (str)            — JWT base role (kept for callers/logging)
      epms_po_subq          — SQLAlchemy subquery of visible EPMS PO ids, or None
      epms_own_uploads (bool) — also show EPMS invoices uploaded by this user
      oa_restrict (bool)    — whether OA invoices are restricted to own uploads
    """
    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])
    codes = await _user_role_codes(db, user_id, role)

    _UNRESTRICTED = {
        "procurement_officer", "procurement_manager", "finance_bp",
        "finance_manager", "ap_clerk", "system_admin",
    }
    if codes & _UNRESTRICTED:
        return {
            "restrict": False,
            "user_id": user_id,
            "role": role,
            "epms_po_subq": None,
            "epms_own_uploads": False,
            "oa_restrict": False,
        }

    # Union of EPMS PO scopes across every restricted role the user holds.
    po_selects: list = []
    epms_own_uploads = False

    if "requester" in codes:
        po_selects.append(await _epms_po_subq_for_user(db, user_id))
        epms_own_uploads = True   # requester also sees their own unmatched uploads

    if codes & {"dept_manager", "dept_admin"}:
        dept_id = await _caller_department_id(db, user)
        if dept_id:
            po_selects.append(await _epms_po_subq_for_dept(db, dept_id))

    # Departments this user oversees, from gm/opm mapping AND (multi-department)
    # director assignment. Director spans multiple departments too, so like GM it
    # must see every mapped department's invoices — not just own uploads. Director
    # is a DERIVED role (approval_dept_routing.director_user_id), NOT a user_roles
    # code, so it is resolved directly here (mirrors epms-api's _director_dept_ids
    # / _effective_role_codes), independent of `codes`. Before this the invoice
    # list had no director branch at all — a director saw only their own uploads.
    oversee_dept_ids: set[uuid.UUID] = set()
    for gm_role in ("gm", "opm"):
        if gm_role in codes:
            oversee_dept_ids.update(await _gm_opm_dept_ids(db, gm_role))
    director_dept_ids = list((await db.execute(sa.text(
        "SELECT dept_id FROM approval_dept_routing WHERE director_user_id = :u"),
        {"u": str(user_id)})).scalars().all())
    is_director = bool(director_dept_ids)
    oversee_dept_ids.update(director_dept_ids)
    if oversee_dept_ids:
        cc_subq = select(EpmsCostCenter.id).where(EpmsCostCenter.department_id.in_(oversee_dept_ids))
        pr_subq = select(EpmsPurchaseRequest.id).where(EpmsPurchaseRequest.cost_center_id.in_(cc_subq))
        po_selects.append(select(EpmsPurchaseOrder.id).where(EpmsPurchaseOrder.pr_id.in_(pr_subq)))

    recognized = (codes & {"requester", "dept_manager", "dept_admin", "gm", "opm"}) or is_director
    if not recognized:
        # Unknown / unrecognised role — restrict to own uploads (unchanged fallback).
        epms_own_uploads = True

    if po_selects:
        epms_po_subq = po_selects[0]
        for extra in po_selects[1:]:
            epms_po_subq = epms_po_subq.union(extra)
    else:
        epms_po_subq = select(EpmsPurchaseOrder.id).where(False)

    return {
        "restrict": True,
        "user_id": user_id,
        "role": role,
        "epms_po_subq": epms_po_subq,
        "epms_own_uploads": epms_own_uploads,
        "oa_restrict": True,
    }


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/all", response_model=UnifiedInvoiceList)
async def list_all_invoices(
    db: SessionDep,
    user: CurrentUserDep,
    search: Optional[str] = Query(None),
    source: Optional[str] = Query(None),  # 'epms' | 'oa' | None=both
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Return invoices from both EPMS and OA in a single paginated list.

    Applies role-based visibility: requester sees own chain; dept_manager sees
    department chain; all other roles see everything.
    """
    scope = await _build_invoice_scope(db, user)
    results: list[UnifiedInvoice] = []

    # ── EPMS invoices ────────────────────────────────────────────────────────
    if source in (None, "epms"):
        q = select(EpmsInvoice)

        if scope["restrict"]:
            epms_conds = []
            if scope["epms_po_subq"] is not None:
                epms_conds.append(EpmsInvoice.po_id.in_(scope["epms_po_subq"]))
            if scope["epms_own_uploads"]:
                epms_conds.append(EpmsInvoice.uploaded_by == scope["user_id"])
            if epms_conds:
                q = q.where(or_(*epms_conds))
            else:
                q = q.where(False)  # no scope → empty

        if search:
            q = q.where(
                or_(
                    EpmsInvoice.vendor_name.ilike(f"%{search}%"),
                    EpmsInvoice.vendor_invoice_number.ilike(f"%{search}%"),
                    EpmsInvoice.internal_ref.ilike(f"%{search}%"),
                )
            )

        epms_rows = (await db.execute(q.order_by(EpmsInvoice.created_at.desc()))).scalars().all()
        for r in epms_rows:
            att_count = (await db.execute(
                select(func.count()).where(
                    InvoiceAttachment.invoice_id == r.id,
                    InvoiceAttachment.invoice_source == "epms",
                )
            )).scalar_one()
            results.append(UnifiedInvoice(
                id=str(r.id),
                source="epms",
                invoice_number=r.vendor_invoice_number,
                vendor_name=r.vendor_name,
                total_amount=r.total_amount,
                currency=r.currency,
                invoice_date=r.invoice_date,
                status=r.status,
                file_name=r.file_name,
                created_at=r.created_at,
                internal_ref=r.internal_ref,
                po_number=r.po_number,
                attachment_count=att_count,
            ))

    # ── OA expense_invoices ──────────────────────────────────────────────────
    if source in (None, "oa"):
        q = select(ExpenseInvoice)

        if scope["oa_restrict"]:
            q = q.where(ExpenseInvoice.created_by == scope["user_id"])

        if search:
            q = q.where(
                or_(
                    ExpenseInvoice.vendor_name.ilike(f"%{search}%"),
                    ExpenseInvoice.invoice_number.ilike(f"%{search}%"),
                )
            )

        oa_rows = (await db.execute(q.order_by(ExpenseInvoice.created_at.desc()))).scalars().all()
        for r in oa_rows:
            att_count = (await db.execute(
                select(func.count()).where(
                    InvoiceAttachment.invoice_id == r.id,
                    InvoiceAttachment.invoice_source == "oa",
                )
            )).scalar_one()
            results.append(UnifiedInvoice(
                id=str(r.id),
                source="oa",
                invoice_number=r.invoice_number,
                vendor_name=r.vendor_name,
                total_amount=r.total_amount,
                currency=r.currency,
                invoice_date=r.invoice_date,
                status=r.status,
                file_name=r.file_name,
                created_at=r.created_at,
                pa_number=r.pa_number,
                attachment_count=att_count,
            ))

    # Sort by created_at desc, paginate
    results.sort(key=lambda x: x.created_at, reverse=True)
    total = len(results)
    start = (page - 1) * page_size
    return UnifiedInvoiceList(items=results[start:start + page_size], total=total)
