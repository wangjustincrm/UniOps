import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import user as user_crud
from app.schemas.user import UserDirectoryResponse, UserListResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserListResponse)
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    role: str | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    items, total = await user_crud.get_all(db, role=role, department_id=department_id, active_only=active_only, search=search, page=page, page_size=page_size)
    return UserListResponse(items=items, total=total)


@router.get("/by-email/{email}", response_model=UserDirectoryResponse)
async def get_user_by_email(email: str, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    u = await user_crud.get_by_email(db, email)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.get("/{user_id}", response_model=UserDirectoryResponse)
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    u = await user_crud.get_by_id(db, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u
