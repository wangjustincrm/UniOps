import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.config import CompanyConfig
from app.models.user import User

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


async def get_role_management(db: AsyncSession) -> dict:
    cfg = await _get_config(db)
    return cfg.role_management if cfg else {}


async def get_dept_gm_opm_mapping(db: AsyncSession) -> dict:
    cfg = await _get_config(db)
    return cfg.dept_gm_opm_mapping if cfg else {}


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
