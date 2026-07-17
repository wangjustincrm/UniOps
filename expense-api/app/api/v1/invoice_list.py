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


async def _build_invoice_scope(db: AsyncSession, user: dict) -> dict:
    """Derive invoice visibility scope from the user's JWT.

    Returns:
      restrict (bool)       — whether any filter applies
      user_id (UUID)
      role (str)
      epms_po_subq          — SQLAlchemy subquery of visible EPMS PO ids, or None
      epms_own_uploads (bool) — requester: also show EPMS invoices uploaded by this user
      oa_restrict (bool)    — whether OA invoices are restricted to own uploads
    """
    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])

    _UNRESTRICTED = {
        "procurement_officer", "procurement_manager", "finance_bp",
        "finance_manager", "ap_clerk", "system_admin",
    }
    if role in _UNRESTRICTED:
        return {
            "restrict": False,
            "user_id": user_id,
            "role": role,
            "epms_po_subq": None,
            "epms_own_uploads": False,
            "oa_restrict": False,
        }

    if role == "requester":
        po_subq = await _epms_po_subq_for_user(db, user_id)
        return {
            "restrict": True,
            "user_id": user_id,
            "role": role,
            "epms_po_subq": po_subq,
            "epms_own_uploads": True,   # also show their own unmatched uploads
            "oa_restrict": True,
        }

    if role in ("dept_manager", "department_admin"):
        dept_id_raw = user.get("department_id")
        if dept_id_raw:
            po_subq = await _epms_po_subq_for_dept(db, uuid.UUID(dept_id_raw))
        else:
            # No department → fall back to own uploads only
            po_subq = select(EpmsPurchaseOrder.id).where(False)
        return {
            "restrict": True,
            "user_id": user_id,
            "role": role,
            "epms_po_subq": po_subq,
            "epms_own_uploads": False,
            "oa_restrict": True,
        }

    if role in ("gm", "opm"):
        # Resolve mapped departments via approval-api's approval_dept_routing
        # (same physical DB, read-only — expense-api never writes it;
        # Portal → Approval Routing is the only writer). This is the single
        # source of truth for dept routing since the phase-3 migration;
        # company_config.dept_gm_opm_mapping is a frozen snapshot kept for
        # rollback (see epms-api/app/core/access_scope.py:_mapped_dept_ids,
        # the reference implementation this mirrors).
        #
        # ★ Semantic change vs the old JSONB: a department with no explicit
        # mapping had NO entry in dept_gm_opm_mapping, so GM never saw it.
        # A department with NO approval_dept_routing row at all (e.g. one
        # mdm-api just created — nothing writes this table for it; only
        # seed_routing.py at seed time and Portal's PUT /routing ever insert
        # rows) is treated as if it were 'gm', via COALESCE. This matches the
        # approval engine's own fallback (approval-api/app/crud/engine.py:
        # `dept_gm_opm.get(str(dept_id), "gm")`), so GM must also see it here
        # — otherwise a brand-new department's invoices are routed to GM for
        # approval but invisible to GM in this list (no task-chain fallback on
        # this endpoint, unlike epms-api's PR/PO/PA scoping — so this gap was
        # worse here: GM couldn't see the invoice at all).
        #
        # ⚠️ SIBLING COPY: epms-api/app/core/access_scope.py's _mapped_dept_ids
        # is the reference implementation this mirrors. If you change this
        # query's semantics, change that one too (this codebase has been
        # bitten before by a sibling copy drifting out of sync — see
        # identity's email.py).
        dept_ids = list((await db.execute(sa.text(
            "SELECT d.id FROM departments d "
            "LEFT JOIN approval_dept_routing r ON r.dept_id = d.id "
            "WHERE d.is_active AND COALESCE(r.gm_or_opm, 'gm') = :r"),
            {"r": role})).scalars().all())
        if dept_ids:
            cc_subq = select(EpmsCostCenter.id).where(
                EpmsCostCenter.department_id.in_(dept_ids)
            )
            pr_subq = select(EpmsPurchaseRequest.id).where(
                EpmsPurchaseRequest.cost_center_id.in_(cc_subq)
            )
            po_subq = select(EpmsPurchaseOrder.id).where(EpmsPurchaseOrder.pr_id.in_(pr_subq))
        else:
            po_subq = select(EpmsPurchaseOrder.id).where(False)
        return {
            "restrict": True,
            "user_id": user_id,
            "role": role,
            "epms_po_subq": po_subq,
            "epms_own_uploads": False,
            "oa_restrict": True,
        }

    # Unknown / unrecognised role — restrict to own uploads
    return {
        "restrict": True,
        "user_id": user_id,
        "role": role,
        "epms_po_subq": select(EpmsPurchaseOrder.id).where(False),
        "epms_own_uploads": True,
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
