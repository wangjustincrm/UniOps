import logging
import uuid
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.config import CompanyConfig
from app.models.user import User

logger = logging.getLogger(__name__)

_DEFAULT_PR = [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]
_DEFAULT_PO = [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]
_DEFAULT_PA = [
    {"id": "finance_bp",      "role": "finance_bp",      "label": "Finance BP Review"},
    {"id": "finance_manager", "role": "finance_manager",  "label": "Finance Manager Approval"},
]


async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    return (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()


async def get_workflow(db: AsyncSession, doc_type: str) -> list[dict]:
    cfg = await _get_config(db)
    if cfg and cfg.workflow_defs:
        nodes = cfg.workflow_defs.get(doc_type, [])
        if nodes:
            return nodes
    defaults = {"pr": _DEFAULT_PR, "po": _DEFAULT_PO, "pa": _DEFAULT_PA}
    return defaults.get(doc_type, [])


_POST_CODES = ("gm", "opm", "vendor_manager", "finance_manager", "procurement_manager")


async def _post_holders(db: AsyncSession) -> dict[str, list[str]]:
    """code -> [user_id str].

    The five SINGLETON posts in _POST_CODES (gm/opm/vendor_manager/
    finance_manager/procurement_manager) are company-unique POSITIONS —
    identity-api enforces one holder each (migration 0003_post_role_singleton
    + the cross-table 409 in PUT /authz/users/{id}/roles). If a user's
    PRIMARY role (users.role) IS the post, they genuinely hold that position,
    so PRIMARY (users.role) UNION ADDITIONAL (user_roles) both count.

    finance_bp is NOT a singleton post — it's a job FUNCTION many people can
    carry (that's exactly why identity exempts it from the singleton index),
    not an assignment. Holding the function is not the same as being the
    curated, assigned approver: reading users.role for finance_bp would
    silently promote every person whose primary role happens to be
    finance_bp into an approver, even if they were never added to the
    assignment list (real prod case: a user with primary role finance_bp who
    is not in the assignment must NOT resolve as an approver). So finance_bp
    holders come from user_roles ONLY.
    """
    out: dict[str, list[str]] = {}
    # ORDER BY makes which holder lands in [0] (get_role_management below)
    # deterministic when the singleton invariant is broken (2+ holders of a
    # post) rather than depending on unordered UNION ALL result order.
    rows = (await db.execute(sa.text(
        "SELECT role AS code, id::text AS uid FROM users WHERE role = ANY(:codes) AND is_active "
        "UNION ALL "
        "SELECT ur.role_code, ur.user_id::text FROM user_roles ur "
        " JOIN users u ON u.id = ur.user_id "
        " WHERE ur.role_code = ANY(:codes_with_bp) AND u.is_active "
        "ORDER BY 1, 2"),
        {"codes": list(_POST_CODES), "codes_with_bp": list(_POST_CODES) + ["finance_bp"]})).all()
    for code, uid in rows:
        out.setdefault(code, []).append(uid)
    for code in _POST_CODES:
        holders = out.get(code) or []
        if len(holders) > 1:
            logger.warning(
                "Singleton post invariant broken: role '%s' resolves to %d holders "
                "(expected 1): %s. Routing/actor-can-approve for this post is "
                "nondeterministic until this is resolved.", code, len(holders), holders,
            )
    return out


async def post_holder_ids(db: AsyncSession, role: str) -> set:
    """Active user ids holding the given post role ('gm' | 'opm' |
    'finance_manager' | 'procurement_manager' | 'vendor_manager' | 'finance_bp').

    Full holder SET (PRIMARY users.role ∪ ADDITIONAL identity user_roles), unlike
    get_role_management()'s `<role>_user_id`, which collapses a post to a SINGLE
    holder (`_post_holders()[role][0]`). Authorization must check membership in
    this set, not equality to that single collapsed holder — otherwise, when the
    singleton invariant is broken (2+ holders, which `_post_holders` warns about),
    only the first-by-uid holder can act and every other legitimate holder is
    wrongly denied.
    """
    holders = await _post_holders(db)
    return {uuid.UUID(u) for u in holders.get(role, [])}


async def get_role_management(db: AsyncSession) -> dict:
    """Legacy-shaped dict, now sourced from user_roles + approval_backups.

    Shape is deliberately identical to the retired company_config.role_management
    JSONB so engine.py's call sites keep working untouched.
    """
    holders = await _post_holders(db)
    rm: dict = {f"{code}_user_id": (holders.get(code) or [None])[0] for code in _POST_CODES}
    rm["finance_bp_user_ids"] = holders.get("finance_bp", [])
    backups = (await db.execute(sa.text(
        "SELECT role_code, backup_user_id::text FROM approval_backups"))).all()
    for code, uid in backups:
        rm[f"{code}_backup_user_id"] = uid
    return rm


async def get_dept_gm_opm_mapping(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT dept_id::text, gm_or_opm FROM approval_dept_routing"))).all()
    return {d: g for d, g in rows}


async def get_dept_director_mapping(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT dept_id::text, director_user_id::text FROM approval_dept_routing "
        "WHERE director_user_id IS NOT NULL"))).all()
    return {d: u for d, u in rows}


async def get_dept_supervisor_enabled(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT dept_id::text FROM approval_dept_routing WHERE supervisor_enabled"))).all()
    return {d: True for (d,) in rows}


async def get_dept_manager_id(
    db: AsyncSession,
    created_by_user_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find the dept_manager for the department of the given user."""
    user_result = await db.execute(select(User.department_id).where(User.id == created_by_user_id))
    dept_id = user_result.scalar_one_or_none()
    if not dept_id:
        return None
    mgr_result = await db.execute(
        select(User.id).where(
            User.role == "dept_manager",
            User.department_id == dept_id,
            User.is_active.is_(True),
        ).limit(1)
    )
    return mgr_result.scalar_one_or_none()


def actor_holds_role(
    role: str,
    actor_id: uuid.UUID,
    rm: dict,
    dept_manager_id: uuid.UUID | None = None,
    dept_gm_opm_mapping: dict | None = None,
    department_id: uuid.UUID | None = None,
) -> bool:
    """Return True if actor_id is the assigned holder of the given workflow role."""
    actor_str = str(actor_id)

    if role == "finance_bp":
        return actor_str in rm.get("finance_bp_user_ids", [])

    if role == "dept_manager":
        return dept_manager_id is not None and actor_id == dept_manager_id

    if role == "gm_or_opm":
        # Resolve via dept_gm_opm_mapping
        mapping = dept_gm_opm_mapping or {}
        resolved = mapping.get(str(department_id)) if department_id else None
        if resolved == "gm":
            role = "gm"
        elif resolved == "opm":
            role = "opm"
        else:
            return False

    role_map = {
        "gm":                   rm.get("gm_user_id"),
        "opm":                  rm.get("opm_user_id"),
        "finance_manager":      rm.get("finance_manager_user_id"),
        "procurement_manager":  rm.get("procurement_manager_user_id"),
        "vendor_manager":       rm.get("vendor_manager_user_id"),
    }
    assigned = role_map.get(role)
    return assigned is not None and assigned == actor_str
