import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud.workflow import (
    get_workflow, get_role_management, get_dept_gm_opm_mapping,
    get_dept_manager_id, actor_holds_role,
)
from app.schemas.resolution import (
    AutoSkippedStep, NextStepRequest, NextStepResponse,
    RoleResolutionRequest, RoleResolutionResponse,
)

router = APIRouter(prefix="/resolve", tags=["resolution"])


@router.post("/role", response_model=RoleResolutionResponse)
async def resolve_role(
    body: RoleResolutionRequest,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    rm = await get_role_management(db)
    mapping = await get_dept_gm_opm_mapping(db)

    role = body.role
    resolved_user_id: uuid.UUID | None = None
    role_label = role.replace("_", " ").title()

    if role == "dept_manager":
        if body.created_by_user_id:
            resolved_user_id = await get_dept_manager_id(db, body.created_by_user_id)
        role_label = "Department Manager"
    elif role == "gm_or_opm":
        dept_str = str(body.department_id) if body.department_id else None
        resolved_role = mapping.get(dept_str, "gm") if dept_str else "gm"
        uid_str = rm.get(f"{resolved_role}_user_id")
        resolved_user_id = uuid.UUID(uid_str) if uid_str else None
        role_label = "GM" if resolved_role == "gm" else "OPM"
    elif role == "finance_bp":
        role_label = "Finance BP"
        # finance_bp is broadcast (no single user) — resolved_user_id stays None
    else:
        uid_str = rm.get(f"{role}_user_id")
        resolved_user_id = uuid.UUID(uid_str) if uid_str else None

    return RoleResolutionResponse(
        resolved_user_id=resolved_user_id,
        assigned_role=role,
        role_label=role_label,
    )


@router.post("/next-step", response_model=NextStepResponse)
async def resolve_next_step(
    body: NextStepRequest,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    workflow = await get_workflow(db, body.doc_type)
    rm = await get_role_management(db)
    mapping = await get_dept_gm_opm_mapping(db)

    dept_mgr_id: uuid.UUID | None = None
    if any(s["role"] == "dept_manager" for s in workflow):
        # Need to find dept_manager — use actor as proxy (they approved current step)
        # In practice the caller should pass created_by_user_id for dept_manager lookup
        pass

    next_step = body.current_step_idx + 1
    auto_skipped: list[AutoSkippedStep] = []

    while next_step < len(workflow):
        step_role = workflow[next_step]["role"]
        holds = actor_holds_role(
            role=step_role,
            actor_id=body.actor_id,
            rm=rm,
            dept_manager_id=dept_mgr_id,
            dept_gm_opm_mapping=mapping,
            department_id=body.department_id,
        )
        if not holds:
            break
        auto_skipped.append(AutoSkippedStep(step_idx=next_step, role=step_role))
        next_step += 1

    is_final = next_step >= len(workflow)
    return NextStepResponse(
        next_step_idx=next_step,
        auto_skipped_steps=auto_skipped,
        is_final_step=is_final,
        workflow_complete=is_final,
    )
