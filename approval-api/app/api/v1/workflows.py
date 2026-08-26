from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud.workflow import get_workflow, get_role_management, get_dept_gm_opm_mapping
from app.schemas.workflow import AllWorkflowDefs, WorkflowDef, WorkflowNodeDef
from app.schemas.resolution import RoleManagementResponse

router = APIRouter(prefix="/workflows", tags=["workflows"])

_DOC_TYPES = ("pr", "po", "pa", "vms_visit", "posign")


@router.get("", response_model=AllWorkflowDefs)
async def get_all_workflows(db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    pr = await get_workflow(db, "pr")
    po = await get_workflow(db, "po")
    pa = await get_workflow(db, "pa")
    return AllWorkflowDefs(
        pr=WorkflowDef(doc_type="pr", steps=[WorkflowNodeDef(**s) for s in pr]),
        po=WorkflowDef(doc_type="po", steps=[WorkflowNodeDef(**s) for s in po]),
        pa=WorkflowDef(doc_type="pa", steps=[WorkflowNodeDef(**s) for s in pa]),
    )


# Static routes MUST be defined before /{doc_type} to avoid being shadowed
@router.get("/role-management", response_model=RoleManagementResponse)
async def role_management(db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    rm = await get_role_management(db)
    return RoleManagementResponse(**rm)


@router.get("/dept-gm-opm-mapping", response_model=dict)
async def dept_gm_opm_mapping(db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    return await get_dept_gm_opm_mapping(db)


@router.get("/{doc_type}", response_model=WorkflowDef)
async def get_workflow_for_doc(doc_type: str, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    if doc_type not in _DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of {_DOC_TYPES}")
    steps = await get_workflow(db, doc_type)
    return WorkflowDef(doc_type=doc_type, steps=[WorkflowNodeDef(**s) for s in steps])


@router.get("/{doc_type}/steps/{step_idx}", response_model=WorkflowNodeDef)
async def get_step(doc_type: str, step_idx: int, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    if doc_type not in _DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of {_DOC_TYPES}")
    steps = await get_workflow(db, doc_type)
    if step_idx < 0 or step_idx >= len(steps):
        raise HTTPException(status_code=404, detail="Step not found")
    return WorkflowNodeDef(**steps[step_idx])
