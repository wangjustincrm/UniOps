"""Factor + FactorValue endpoints, plus the reusable Factor Library."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.crud import catalog as catalog_crud
from app.crud import factor as factor_crud
from app.crud import settings as settings_crud
from app.schemas.factor import (
    FactorCreate, FactorResponse, FactorUpdate,
    FactorValueCreate, FactorValueResponse, FactorValueUpdate,
    FactorFromTemplate,
    FactorTemplateCreate, FactorTemplateResponse, FactorTemplateUpdate,
    FactorTemplateValueCreate, FactorTemplateValueResponse, FactorTemplateValueUpdate,
)

router = APIRouter(tags=["factor"])

_WRITE_ROLES = ("system_admin", "finance_manager", "finance_bp")


@router.get("/accounts/{account_id}/factors", response_model=list[FactorResponse])
async def list_factors(
    account_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    acct = await catalog_crud.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    factors = await factor_crud.list_factors_for_account(db, account_id)
    return [FactorResponse.model_validate(f) for f in factors]


@router.post(
    "/accounts/{account_id}/factors",
    response_model=FactorResponse, status_code=status.HTTP_201_CREATED,
)
async def create_factor(
    account_id: uuid.UUID, payload: FactorCreate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    acct = await catalog_crud.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if not acct.decomposition_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Account.decomposition_enabled must be True before adding factors",
        )
    s = await settings_crud.get_settings(db)
    factor = await factor_crud.create_factor(
        db, account_id, payload, max_allowed=s.max_factors_per_account,
    )
    return FactorResponse.model_validate(factor)


@router.post(
    "/accounts/{account_id}/factors/from-template",
    response_model=FactorResponse, status_code=status.HTTP_201_CREATED,
)
async def create_factor_from_template(
    account_id: uuid.UUID, payload: FactorFromTemplate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    """Create a per-Account factor by cloning a Factor Library template.

    Copies factor_code/name (optionally overridden) and active values into the
    per-Account tables. No live link is kept — later template edits do not
    propagate. Same 3-factor Account cap and code-collision checks apply.
    """
    acct = await catalog_crud.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if not acct.decomposition_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Account.decomposition_enabled must be True before adding factors",
        )
    s = await settings_crud.get_settings(db)
    factor = await factor_crud.create_factor_from_template(
        db, account_id, payload, max_allowed=s.max_factors_per_account,
    )
    return FactorResponse.model_validate(factor)


@router.patch("/factors/{factor_id}", response_model=FactorResponse)
async def update_factor(
    factor_id: uuid.UUID, payload: FactorUpdate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    factor = await factor_crud.get_factor(db, factor_id)
    if factor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor not found")
    factor = await factor_crud.update_factor(db, factor, payload)
    return FactorResponse.model_validate(factor)


@router.delete("/factors/{factor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_factor(
    factor_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    factor = await factor_crud.get_factor(db, factor_id)
    if factor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor not found")
    await factor_crud.delete_factor(db, factor)


@router.post(
    "/factors/{factor_id}/values",
    response_model=FactorValueResponse, status_code=status.HTTP_201_CREATED,
)
async def create_factor_value(
    factor_id: uuid.UUID, payload: FactorValueCreate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    v = await factor_crud.create_factor_value(db, factor_id, payload)
    return FactorValueResponse.model_validate(v)


@router.patch("/factor-values/{value_id}", response_model=FactorValueResponse)
async def update_factor_value(
    value_id: uuid.UUID, payload: FactorValueUpdate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    v = await factor_crud.get_factor_value(db, value_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor value not found")
    v = await factor_crud.update_factor_value(db, v, payload)
    return FactorValueResponse.model_validate(v)


@router.delete("/factor-values/{value_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_factor_value(
    value_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    v = await factor_crud.get_factor_value(db, value_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor value not found")
    await factor_crud.delete_factor_value(db, v)


# ── Factor Library (Portal → Finance → Factor Library) ────────────────────────

@router.get("/factor-templates", response_model=list[FactorTemplateResponse])
async def list_factor_templates(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    active_only: bool = Query(default=False),
):
    items = await factor_crud.list_templates(db, active_only=active_only)
    return [FactorTemplateResponse.model_validate(t) for t in items]


@router.get("/factor-templates/{template_id}", response_model=FactorTemplateResponse)
async def get_factor_template(
    template_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    t = await factor_crud.get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template not found")
    return FactorTemplateResponse.model_validate(t)


@router.post(
    "/factor-templates", response_model=FactorTemplateResponse, status_code=status.HTTP_201_CREATED,
)
async def create_factor_template(
    payload: FactorTemplateCreate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    t = await factor_crud.create_template(db, payload)
    return FactorTemplateResponse.model_validate(t)


@router.patch("/factor-templates/{template_id}", response_model=FactorTemplateResponse)
async def update_factor_template(
    template_id: uuid.UUID, payload: FactorTemplateUpdate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    t = await factor_crud.get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template not found")
    t = await factor_crud.update_template(db, t, payload)
    return FactorTemplateResponse.model_validate(t)


@router.delete("/factor-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_factor_template(
    template_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    t = await factor_crud.get_template(db, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template not found")
    await factor_crud.delete_template(db, t)


@router.post(
    "/factor-templates/{template_id}/values",
    response_model=FactorTemplateValueResponse, status_code=status.HTTP_201_CREATED,
)
async def create_factor_template_value(
    template_id: uuid.UUID, payload: FactorTemplateValueCreate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    v = await factor_crud.create_template_value(db, template_id, payload)
    return FactorTemplateValueResponse.model_validate(v)


@router.patch(
    "/factor-template-values/{value_id}", response_model=FactorTemplateValueResponse,
)
async def update_factor_template_value(
    value_id: uuid.UUID, payload: FactorTemplateValueUpdate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),  # noqa: ARG001
):
    v = await factor_crud.get_template_value(db, value_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template value not found")
    v = await factor_crud.update_template_value(db, v, payload)
    return FactorTemplateValueResponse.model_validate(v)


@router.delete(
    "/factor-template-values/{value_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_factor_template_value(
    value_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    v = await factor_crud.get_template_value(db, value_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template value not found")
    await factor_crud.delete_template_value(db, v)
