from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud.engine import (CONDITIONAL_ROLES, _OB_MODE_LABELS, _get_config,
                             _get_workflow, _ob_prefix, config_ob_mode)
from app.crud.workflow import get_workflow, get_role_management, get_dept_gm_opm_mapping
from app.schemas.workflow import AllWorkflowDefs, OverBudgetRule, WorkflowDef, WorkflowNodeDef
from app.schemas.resolution import RoleManagementResponse

router = APIRouter(prefix="/workflows", tags=["workflows"])

_DOC_TYPES = ("pr", "po", "pa", "vms_visit", "posign")
# What the per-type read-out can describe: every document type the engine runs
# a chain for. Wider than _DOC_TYPES, which the single-step lookup below keeps.
_READ_OUT_TYPES = ("pr", "po", "posign", "agr", "pa", "pa_frt", "pa_dir", "exp", "mil",
                   "trv", "tra", "budget_plan", "vms_visit")


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
    """The chain the engine actually walks for this kind of document.

    Read through engine._get_workflow — the function execution uses — not
    crud.workflow.get_workflow, whose fallback defaults had drifted from the
    engine's (PORTAL.md §14 PO-37), so the Assistant described a chain that
    does not run and could not describe agreements or OA claims at all.
    """
    if doc_type not in _READ_OUT_TYPES:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of {_READ_OUT_TYPES}")
    steps = await _get_workflow(db, doc_type)
    # Mark the steps that do not always run. The stored definition has no such
    # flag — _should_skip_step decides at runtime — so anything describing this
    # process to a person would otherwise present every step as mandatory.
    annotated = []
    for st in steps:
        node = dict(st)
        condition = CONDITIONAL_ROLES.get(node.get("role"))
        if condition:
            node["conditional"] = True
            node["condition"] = condition
        annotated.append(node)
    over_budget = None
    if doc_type == "pr":
        mode = config_ob_mode(await _get_config(db))
        over_budget = OverBudgetRule(
            mode=mode,
            mode_label=_OB_MODE_LABELS.get(mode, mode),
            added_steps=[WorkflowNodeDef(**n) for n in _ob_prefix(mode)],
            blocks_submission=mode == "hard_block",
            # The form's rule, not the engine's: PrCreatePage/PrEditPage refuse
            # an over-budget PR with an empty justification unless the mode is
            # hard_block (then it cannot be submitted at all).
            justification_required=mode != "hard_block",
            note=("Applies only to a PR whose total exceeds the Available balance of "
                  "its cost centre and budget account. The mode is read when the PR "
                  "is submitted and kept for that PR; changing it later never adds "
                  "steps to a PR already submitted, and a looser mode skips any "
                  "over-budget step it is still waiting on. Set in Finance → Budget "
                  "Config → Over-Budget Approval Mode."),
        )
    return WorkflowDef(doc_type=doc_type,
                       steps=[WorkflowNodeDef(**n) for n in annotated],
                       over_budget=over_budget)


@router.get("/{doc_type}/steps/{step_idx}", response_model=WorkflowNodeDef)
async def get_step(doc_type: str, step_idx: int, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    if doc_type not in _DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of {_DOC_TYPES}")
    steps = await get_workflow(db, doc_type)
    if step_idx < 0 or step_idx >= len(steps):
        raise HTTPException(status_code=404, detail="Step not found")
    return WorkflowNodeDef(**steps[step_idx])
