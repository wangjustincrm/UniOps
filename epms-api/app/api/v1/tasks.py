"""Task inbox endpoints."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import task as task_crud
from app.schemas.task import TaskListResponse, TaskResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    db: SessionDep,
    user: CurrentUserPayload,
    is_completed: bool | None = None,
):
    """Return tasks for the current user (by role + personal assignment)."""
    items = await task_crud.get_for_role(
        db,
        role=user.get("role", ""),
        user_id=uuid.UUID(user["sub"]),
        is_completed=is_completed,
    )
    return {"items": items, "total": len(items)}


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(task_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    task = await task_crud.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.is_completed:
        raise HTTPException(status_code=409, detail="Task already completed")
    task.is_completed = True
    task.completed_at = datetime.now(timezone.utc)
    task.completed_by = uuid.UUID(user["sub"])
    await db.flush()
    await db.refresh(task)
    return task
