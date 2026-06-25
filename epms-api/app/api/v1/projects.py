"""Project endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.crud import project as project_crud
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])

AdminDep = Annotated[dict, Depends(require_roles("system_admin"))]


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: SessionDep,
    _: CurrentUserPayload,
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    return await project_crud.get_all(db, status=status, search=search)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, db: SessionDep, _: AdminDep):
    if await project_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=409, detail="Project code already exists")
    return await project_crud.create(db, body)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    project = await project_crud.get_by_id(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: uuid.UUID, body: ProjectUpdate, db: SessionDep, _: AdminDep):
    project = await project_crud.get_by_id(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return await project_crud.update(db, project, body)
