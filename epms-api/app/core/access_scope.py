"""Role-based document visibility scope builder for EPMS list endpoints.

Visibility rules:
  requester          → own PRs + linked POs / GRs / Invoices / PAs
  dept_manager /
  department_admin   → department's PRs + linked POs / GRs / Invoices / PAs
  gm / opm           → mapped departments' PRs + linked chain
  all other roles    → unrestricted (see everything)

Multi-role users: the JWT carries only the user's single base role. Special roles
(procurement_manager, gm, opm, finance_manager, vendor_manager, finance_bp) are
ADDITIONAL roles held in identity's user_roles table (same physical DB — phase 3
retired the old CompanyConfig.role_management assignments). If a user holds ANY
unrestricted special role, they get unrestricted scope regardless of their base role.
"""
import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.cost_center import CostCenter
from app.models.pr import PurchaseRequest
from app.models.po import PurchaseOrder
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User


def _open_task_doc_ids(user_id: uuid.UUID, doc_type: str) -> Select:
    """Doc ids the user has an OPEN (uncompleted) task assigned for. OR-ing this
    into the visibility scope keeps task assignment and document visibility
    consistent: if you are asked to approve a document, you can open it — even
    when approval routing lands outside your normal department/cost-center scope
    (e.g. a PR whose creator has no department, escalated to a fallback approver)."""
    return select(Task.document_id).where(
        Task.assigned_user_id == user_id,
        Task.document_type == doc_type,
        Task.is_completed.is_(False),
    )


def _task_chain_pr_ids(user_id: uuid.UUID) -> Select:
    """PR ids reachable from any of the user's open tasks, walking UP the chain so
    document-chain navigation never 404s: a PR task → the PR; a PO task → its
    parent PR; a PA task → PA→PO→parent PR. (You can see a PO you must approve, so
    you can open the PR it came from.)"""
    po_from_pa = select(PaymentApplication.po_id).where(
        PaymentApplication.id.in_(_open_task_doc_ids(user_id, "pa")))
    return _open_task_doc_ids(user_id, "pr").union(
        select(PurchaseOrder.pr_id).where(
            PurchaseOrder.id.in_(_open_task_doc_ids(user_id, "po")),
            PurchaseOrder.pr_id.isnot(None)),
        select(PurchaseOrder.pr_id).where(
            PurchaseOrder.id.in_(po_from_pa), PurchaseOrder.pr_id.isnot(None)),
    )


def _task_chain_po_ids(user_id: uuid.UUID) -> Select:
    """PO ids reachable from the user's open tasks: a PO task → the PO; a PA task
    → its parent PO."""
    po_from_pa = select(PaymentApplication.po_id).where(
        PaymentApplication.id.in_(_open_task_doc_ids(user_id, "pa")))
    return _open_task_doc_ids(user_id, "po").union(po_from_pa)

# Roles whose scope is restricted to their department / own documents.
# Every role NOT in this set gets unrestricted visibility.
_RESTRICTED_ROLES = {"requester", "dept_manager", "department_admin", "gm", "opm", "supervisor", "director"}


async def _user_dept_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    row = (await db.execute(select(User.department_id).where(User.id == user_id))).scalar_one_or_none()
    return row


async def _dept_cc_subq(dept_ids: list[uuid.UUID]):
    """Subquery: cost_center IDs for the given departments."""
    return select(CostCenter.id).where(CostCenter.department_id.in_(dept_ids))


async def _mapped_dept_ids(db: AsyncSession, role: str) -> list[uuid.UUID]:
    """Return department IDs whose gm_or_opm step routes to this role.

    Reads approval-api's approval_dept_routing (same physical DB, read-only —
    epms never writes it; Portal → Approval Routing is the only writer). This is
    the single source of truth for dept routing since the phase-3 migration;
    company_config.dept_gm_opm_mapping is a frozen snapshot kept for rollback.

    A department with NO routing row at all (e.g. one mdm-api just created —
    nothing writes approval_dept_routing for it; only seed_routing.py at seed
    time and Portal's PUT /routing ever insert rows) is treated as if it were
    'gm', via COALESCE. This matches the approval engine's own fallback
    (approval-api/app/crud/engine.py: `dept_gm_opm.get(str(dept_id), "gm")`,
    used at the PR/PO/PA routing steps) — a department with no row is routed to
    GM for approval, so GM must also be able to see it here. Before this fix
    the query read approval_dept_routing alone (no departments join), so a
    dept with no row was invisible to GM despite the engine routing its
    approval there — a new-department blind spot for GM.

    ⚠️ SIBLING COPY: expense-api/app/api/v1/invoice_list.py's gm/opm dept_ids
    block (~line 130) is a structural copy of this query — this file is the
    "reference implementation" its own comment names. If you change this
    query's semantics, change that one too (this codebase has been bitten
    before by a sibling copy drifting out of sync — see identity's email.py).
    """
    # 不过滤 d.is_active —— 这是有意的,别"顺手补上":
    # 旧的 JSONB 实现和 engine.py 的 .get(dept, "gm") 都不看部门是否停用。
    # 加上它会让**停用部门的在途单据对 GM 消失**(部门重组但 PO/发票未结时会真的发生),
    # 而 expense-api 那侧没有 task-chain 兜底,GM 会完全瞎掉。宁可让 GM 多看见已停用
    # 部门的遗留单据(旧代码一直如此,无害),也不要让他看不见还需要他处理的单据。
    rows = (await db.execute(text(
        "SELECT d.id FROM departments d "
        "LEFT JOIN approval_dept_routing r ON r.dept_id = d.id "
        "WHERE COALESCE(r.gm_or_opm, 'gm') = :r"),
        {"r": role},
    )).scalars().all()
    return list(rows)


async def _director_dept_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Return department IDs for which this user is the mapped director.

    Same source/ownership rules as _mapped_dept_ids above.
    """
    rows = (await db.execute(
        text("SELECT dept_id FROM approval_dept_routing WHERE director_user_id = :u"),
        {"u": str(user_id)},
    )).scalars().all()
    return list(rows)


async def _effective_role_codes(
    db: AsyncSession,
    base_role: str,
    user_id: uuid.UUID,
) -> set[str]:
    """Return the full set of active role codes for a user.

    The JWT base role plus any ADDITIONAL roles from identity's user_roles
    (same physical DB — read directly, no HTTP). Replaces the retired
    company_config.role_management assignments (phase 3).
    """
    codes: set[str] = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)

    uid_str = str(user_id)
    # director/supervisor derivation is unrelated to role_management/user_roles.
    # director now sourced from approval-api's approval_dept_routing.director_user_id
    # (same physical DB, read-only — epms never writes it), matching _director_dept_ids
    # above; company_config.dept_director_mapping is a frozen snapshot no longer read
    # here (nothing writes it anymore since Portal → Approval Routing moved to the
    # new table — phase 3). supervisor stays on User.supervisor_id.
    is_director = (await db.execute(sa.text(
        "SELECT 1 FROM approval_dept_routing WHERE director_user_id = :u LIMIT 1"),
        {"u": uid_str})).scalar_one_or_none()
    if is_director is not None:
        codes.add("director")
    is_supervisor = (await db.execute(
        select(User.id).where(User.supervisor_id == user_id).limit(1)
    )).scalar_one_or_none()
    if is_supervisor is not None:
        codes.add("supervisor")
    return codes


_POST_CODES = ("gm", "opm", "finance_manager", "procurement_manager", "vendor_manager", "finance_bp")


async def role_holder_ids(
    db: AsyncSession,
    codes: tuple[str, ...] = _POST_CODES,
) -> dict[str, set[uuid.UUID]]:
    """code -> set of active user ids holding that role.

    A post can be held as a PRIMARY role (users.role) or an ADDITIONAL role
    (identity's user_roles, same physical DB) — both count. Mirrors
    approval-api's workflow._post_holders pattern. Used by po.py/pr.py's
    approval auto-skip logic (replaces the retired single
    company_config.role_management.<role>_user_id fields — phase 3).
    """
    rows = (await db.execute(sa.text(
        "SELECT role AS code, id::text AS uid FROM users WHERE role = ANY(:codes) AND is_active "
        "UNION ALL "
        "SELECT ur.role_code, ur.user_id::text FROM user_roles ur "
        " JOIN users u ON u.id = ur.user_id "
        " WHERE ur.role_code = ANY(:codes) AND u.is_active"),
        {"codes": list(codes)})).all()
    out: dict[str, set[uuid.UUID]] = {}
    for code, uid in rows:
        out.setdefault(code, set()).add(uuid.UUID(uid))
    return out


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
    """Union of all permissions across the user's roles.

    Reads identity's matrix directly via the shared authz package (same
    physical DB — no HTTP, no token). Phase 1's authz_client (HTTP + cache +
    outage fallback + write-through mirror) existed only because this pure-DB
    call chain had no token to call identity with; that whole apparatus is
    gone — a DB outage is the only thing that can stop this now, and that
    stops everything anyway.
    """
    from uniops_authz import effective_permissions
    return await effective_permissions(db, user_id, base_role)


# ── Public helpers ─────────────────────────────────────────────────────────────

async def visible_pr_subquery(
    db: AsyncSession,
    user: dict,
) -> Optional[Select]:
    """Return a scalar subquery of visible PR ids, or None (= no filter = all).

    Multi-role users: visibility is the UNION of every role the user holds
    (JWT base role + ADDITIONAL roles in identity's user_roles). Two cases:

      • If ANY held role is unrestricted (procurement_manager, finance_manager,
        …), the user gets full visibility (None) regardless of the others.
      • Otherwise the user's scope is the OR of every RESTRICTED role's own
        scope. This is what lets a user who is BOTH Department Manager AND GM
        (hanchenggang) see their own department's PRs *and* the PRs of the
        departments their GM role covers — before this, the function branched
        on the single JWT base role and silently dropped the second role's
        scope (a dept_manager+gm saw only their department). gm/dept_manager
        are both RESTRICTED, so the unrestricted shortcut above never rescued
        this multi-restricted-role case.

    Per-role scope is unchanged from the previous single-role branches; they
    are just OR-ed together here instead of being selected by base role.
    """
    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])
    codes = await _effective_role_codes(db, role, user_id)

    # Any unrestricted role → full visibility.
    if any(c not in _RESTRICTED_ROLES for c in codes):
        return None  # unrestricted

    # Union of scopes across every restricted role the user actually holds.
    task_pr = _task_chain_pr_ids(user_id)
    # task-chain is always OR-ed in: if you hold an open approval task for a PR
    # (routing may land it outside your dept/cost-center scope), you can see it.
    conds = [PurchaseRequest.id.in_(task_pr)]
    cc_dept_ids: set[uuid.UUID] = set()       # cost-center oversight (dept_manager/gm/opm/director)
    creator_dept_ids: set[uuid.UUID] = set()  # creator's-department membership (dept_manager/director)

    if "requester" in codes:
        conds.append(PurchaseRequest.created_by == user_id)

    if codes & {"dept_manager", "department_admin"}:
        # A dept_manager sees PRs charged to their department's cost centers
        # (budget oversight) OR raised by a member of their department. (b) is
        # required to match approval routing: the engine assigns approve_pr by
        # the *requester's* department (approval-api _get_dept_manager_id), so
        # without it a manager gets the inbox task but 404s on GET /pr/{id} when
        # the PR is charged to a cost center outside their dept (PR-20260620-0001).
        dept_id = await _user_dept_id(db, user_id)
        if dept_id:
            cc_dept_ids.add(dept_id)
            creator_dept_ids.add(dept_id)

    for gm_role in ("gm", "opm"):
        if gm_role in codes:
            cc_dept_ids.update(await _mapped_dept_ids(db, gm_role))

    if "director" in codes:
        for d in await _director_dept_ids(db, user_id):
            cc_dept_ids.add(d)
            creator_dept_ids.add(d)

    if "supervisor" in codes:
        reports = select(User.id).where(User.supervisor_id == user_id)
        conds.append(PurchaseRequest.created_by.in_(reports))

    if cc_dept_ids:
        conds.append(PurchaseRequest.cost_center_id.in_(
            select(CostCenter.id).where(CostCenter.department_id.in_(cc_dept_ids))))
    if creator_dept_ids:
        conds.append(PurchaseRequest.created_by.in_(
            select(User.id).where(User.department_id.in_(creator_dept_ids))))

    return select(PurchaseRequest.id).where(or_(*conds))


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

    task_po = _task_chain_po_ids(user_id)

    if role == "requester":
        # POs linked to requester's PRs, POs they created, or POs assigned to them.
        return select(PurchaseOrder.id).where(
            or_(
                PurchaseOrder.pr_id.in_(pr_subq),
                PurchaseOrder.created_by == user_id,
                PurchaseOrder.id.in_(task_po),
            )
        )

    # dept_manager / gm / opm: POs linked to their PRs, plus any assigned to them.
    return select(PurchaseOrder.id).where(
        or_(PurchaseOrder.pr_id.in_(pr_subq), PurchaseOrder.id.in_(task_po))
    )


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
    # Assigned an open approval task for this PA → always visible (task↔visibility).
    has_task = (await db.execute(
        select(Task.id).where(
            Task.assigned_user_id == scope["user_id"],
            Task.document_type == "pa",
            Task.document_id == pa.id,
            Task.is_completed.is_(False),
        ).limit(1)
    )).scalar_one_or_none()
    if has_task is not None:
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
