"""Role-based document visibility scope builder for EPMS list endpoints.

Visibility rules:
  requester          → own PRs + linked POs / GRs / Invoices / PAs
  dept_manager /
  department_admin   → department's PRs + linked POs / GRs / Invoices / PAs
  gm / opm           → mapped departments' PRs + linked chain
  all other roles    → unrestricted (see everything)

Multi-role users: the JWT carries only the user's single base role. Special roles
(procurement_manager, gm, opm, finance_manager, vendor_manager, finance_bp) are
secondary assignments in CompanyConfig.role_management. If a user holds ANY
unrestricted special role, they get unrestricted scope regardless of their base role.
"""
import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.cost_center import CostCenter
from app.models.pr import PurchaseRequest
from app.models.po import PurchaseOrder
from app.models.user import User
from app.models.config import CompanyConfig

# Roles whose scope is restricted to their department / own documents.
# Every role NOT in this set gets unrestricted visibility.
_RESTRICTED_ROLES = {"requester", "dept_manager", "department_admin", "gm", "opm"}


async def _user_dept_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    row = (await db.execute(select(User.department_id).where(User.id == user_id))).scalar_one_or_none()
    return row


async def _dept_cc_subq(dept_ids: list[uuid.UUID]):
    """Subquery: cost_center IDs for the given departments."""
    return select(CostCenter.id).where(CostCenter.department_id.in_(dept_ids))


async def _mapped_dept_ids(db: AsyncSession, role: str) -> list[uuid.UUID]:
    """Return department IDs mapped to this GM/OPM role via CompanyConfig."""
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    if not cfg or not cfg.dept_gm_opm_mapping:
        return []
    return [
        uuid.UUID(dept_id)
        for dept_id, mapped_role in cfg.dept_gm_opm_mapping.items()
        if mapped_role == role
    ]


async def _effective_role_codes(
    db: AsyncSession,
    base_role: str,
    user_id: uuid.UUID,
) -> set[str]:
    """Return the full set of active role codes for a user.

    Starts with the JWT base role, then adds any special-role assignments from
    CompanyConfig.role_management (gm, opm, finance_manager, procurement_manager,
    vendor_manager, finance_bp).
    """
    codes: set[str] = {base_role} if base_role else set()
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    if not cfg or not cfg.role_management:
        return codes
    rm: dict = cfg.role_management
    uid_str = str(user_id)
    single_role_fields = {
        "gm":                  rm.get("gm_user_id"),
        "opm":                 rm.get("opm_user_id"),
        "finance_manager":     rm.get("finance_manager_user_id"),
        "procurement_manager": rm.get("procurement_manager_user_id"),
        "vendor_manager":      rm.get("vendor_manager_user_id"),
    }
    for special_role, assigned_uid in single_role_fields.items():
        if assigned_uid == uid_str:
            codes.add(special_role)
    if uid_str in rm.get("finance_bp_user_ids", []):
        codes.add("finance_bp")
    return codes


async def _has_unrestricted_special_role(
    db: AsyncSession,
    base_role: str,
    user_id: uuid.UUID,
) -> bool:
    """Return True if any of the user's active roles is unrestricted."""
    codes = await _effective_role_codes(db, base_role, user_id)
    return any(c not in _RESTRICTED_ROLES for c in codes)


async def _effective_permissions(
    db: AsyncSession,
    base_role: str,
    user_id: uuid.UUID,
) -> dict[str, bool]:
    """Union of all permissions across the user's active roles.

    Reads the effective role-permission matrix from CompanyConfig (merged with
    defaults via get_effective_role_permissions) and ORs the perms together for
    every role the user holds. A permission is granted if ANY active role has it.
    """
    from app.crud.config import get_effective_role_permissions, PERMISSION_KEYS

    codes = await _effective_role_codes(db, base_role, user_id)
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    if cfg is None:
        return {k: False for k in PERMISSION_KEYS}
    matrix = get_effective_role_permissions(cfg)
    merged: dict[str, bool] = {k: False for k in PERMISSION_KEYS}
    for code in codes:
        role_perms = matrix.get(code, {})
        for k in PERMISSION_KEYS:
            if role_perms.get(k):
                merged[k] = True
    return merged


# ── Public helpers ─────────────────────────────────────────────────────────────

async def visible_pr_subquery(
    db: AsyncSession,
    user: dict,
) -> Optional[Select]:
    """Return a scalar subquery of visible PR ids, or None (= no filter = all).

    Multi-role users: if the user holds any unrestricted special role via Role
    Management (e.g. base=dept_manager but also procurement_manager), they get
    unrestricted scope (None) regardless of their base role.
    """
    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])

    # Multi-role expansion: if the user holds any unrestricted special role,
    # grant full visibility regardless of the base JWT role.
    if await _has_unrestricted_special_role(db, role, user_id):
        return None  # unrestricted

    if role == "requester":
        return select(PurchaseRequest.id).where(PurchaseRequest.created_by == user_id)

    if role in ("dept_manager", "department_admin"):
        dept_id = await _user_dept_id(db, user_id)
        if not dept_id:
            return select(PurchaseRequest.id).where(False)  # empty result
        # A PR is visible to a dept_manager if EITHER:
        #   (a) it is charged to one of their department's cost centers (budget
        #       oversight), OR
        #   (b) it was raised by a member of their department (creator's
        #       User.department_id == this manager's department).
        # (b) is required to match approval routing: the approval engine assigns
        # the approve_pr task by the *requester's* department
        # (approval-api _get_dept_manager_id), so without it a manager receives
        # the inbox task but 404s on GET /pr/{id} whenever the PR is charged to a
        # cost center outside their department (PR-20260620-0001).
        cc_subq = select(CostCenter.id).where(CostCenter.department_id == dept_id)
        creator_subq = select(User.id).where(User.department_id == dept_id)
        return select(PurchaseRequest.id).where(
            or_(
                PurchaseRequest.cost_center_id.in_(cc_subq),
                PurchaseRequest.created_by.in_(creator_subq),
            )
        )

    if role in ("gm", "opm"):
        dept_ids = await _mapped_dept_ids(db, role)
        if not dept_ids:
            return select(PurchaseRequest.id).where(False)
        cc_subq = select(CostCenter.id).where(CostCenter.department_id.in_(dept_ids))
        return select(PurchaseRequest.id).where(PurchaseRequest.cost_center_id.in_(cc_subq))

    return None  # unrestricted


async def visible_po_subquery(
    db: AsyncSession,
    user: dict,
    pr_subq: Optional[Select],
) -> Optional[Select]:
    """Return a scalar subquery of visible PO ids, or None (= no filter)."""
    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])

    if pr_subq is None:
        return None  # unrestricted

    if role == "requester":
        # POs linked to requester's PRs, OR POs created directly by the requester
        from sqlalchemy import or_
        return select(PurchaseOrder.id).where(
            or_(
                PurchaseOrder.pr_id.in_(pr_subq),
                PurchaseOrder.created_by == user_id,
            )
        )

    # dept_manager / gm / opm: only POs linked to their PRs
    return select(PurchaseOrder.id).where(PurchaseOrder.pr_id.in_(pr_subq))


async def is_pr_visible(db: AsyncSession, pr_id: uuid.UUID, scope: dict) -> bool:
    """True if the given PR id falls inside the user's pr_subq (or unrestricted)."""
    if not _scope_allows_view(scope, "view_pr"):
        return False
    pr_subq = scope["pr_subq"]
    if pr_subq is None:
        return True
    from app.models.pr import PurchaseRequest as _PR
    row = (await db.execute(
        select(_PR.id).where(_PR.id == pr_id).where(_PR.id.in_(pr_subq))
    )).scalar_one_or_none()
    return row is not None


async def is_po_visible(db: AsyncSession, po_id: uuid.UUID, scope: dict) -> bool:
    """True if the given PO id falls inside the user's po_subq (or unrestricted)."""
    if not _scope_allows_view(scope, "view_po"):
        return False
    po_subq = scope["po_subq"]
    if po_subq is None:
        return True
    row = (await db.execute(
        select(PurchaseOrder.id).where(PurchaseOrder.id == po_id).where(PurchaseOrder.id.in_(po_subq))
    )).scalar_one_or_none()
    return row is not None


async def is_gr_visible(db: AsyncSession, gr, scope: dict) -> bool:
    """True if the given GR's po_id falls inside the user's po_subq (or unrestricted)."""
    if not _scope_allows_view(scope, "view_gr"):
        return False
    po_subq = scope["po_subq"]
    if po_subq is None:
        return True
    if gr.po_id is None:
        return False
    row = (await db.execute(
        select(PurchaseOrder.id).where(PurchaseOrder.id == gr.po_id).where(PurchaseOrder.id.in_(po_subq))
    )).scalar_one_or_none()
    return row is not None


async def is_pa_visible(db: AsyncSession, pa, scope: dict) -> bool:
    """True if PA is in chain (po_subq) OR — for requester — was created by them."""
    if not _scope_allows_view(scope, "view_pa"):
        return False
    po_subq = scope["po_subq"]
    if po_subq is None:
        return True
    # Requester direct creation: PA created_by themselves is always visible to them.
    if scope["role"] == "requester" and pa.created_by == scope["user_id"]:
        return True
    if pa.po_id is None:
        return False
    row = (await db.execute(
        select(PurchaseOrder.id).where(PurchaseOrder.id == pa.po_id).where(PurchaseOrder.id.in_(po_subq))
    )).scalar_one_or_none()
    return row is not None


async def build_scope(db: AsyncSession, user: dict) -> dict:
    """Resolve access scope for a user.

    Returns a dict:
      {
        "pr_subq":  Select | None,  # scalar subquery of visible PR ids
        "po_subq":  Select | None,  # scalar subquery of visible PO ids
        "user_id":  uuid.UUID,
        "role":     str,
        "restrict": bool,           # False = unrestricted (see all)
        "perms":    dict[str, bool],# union of effective permissions across active roles
      }

    `perms` includes the view_pr / view_po / view_gr / view_invoice / view_pa keys
    sourced from the Access Control Matrix (Admin Panel). Endpoints should treat
    a missing/false view_<doc> as "no list visibility" regardless of pr/po_subq.
    """
    pr_subq = await visible_pr_subquery(db, user)
    po_subq = await visible_po_subquery(db, user, pr_subq)
    user_id = uuid.UUID(user["sub"])
    perms = await _effective_permissions(db, user.get("role", ""), user_id)
    return {
        "pr_subq":  pr_subq,
        "po_subq":  po_subq,
        "user_id":  user_id,
        "role":     user.get("role", ""),
        "restrict": pr_subq is not None,
        "perms":    perms,
    }


def _scope_allows_view(scope: dict, view_key: str) -> bool:
    """Return True if the view_<doc> permission is granted in this scope.

    Tolerates older scopes built before the `perms` field existed (returns True).
    """
    perms = scope.get("perms")
    if perms is None:
        return True
    return bool(perms.get(view_key, False))
