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
    """Dismiss a NON-approval task from the inbox.

    Approval tasks are excluded on purpose. This endpoint only flips
    `is_completed`; it does not run the approval engine, so completing an
    `approve_*` task here writes no ApprovalEvent and does not advance the
    document. The document then sits in_review with zero open approve tasks, and
    because the Approve button is gated purely on holding an open approve task
    (system_admin included), it becomes unapprovable by anyone — recoverable only
    by an admin re-running routing/resync-document. That is exactly how
    PO-226-2608-01 (and 2 PRs + 2 EXPs) were stranded via the old "Mark Done"
    button, which is why the button is gone and this door is bolted.
    """
    task = await task_crud.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.type.startswith("approve"):
        raise HTTPException(
            status_code=409,
            detail="Approval tasks cannot be dismissed. Approve, return or reject "
                   "the document instead — the task closes automatically.",
        )
    if task.is_completed:
        raise HTTPException(status_code=409, detail="Task already completed")
    task.is_completed = True
    task.completed_at = datetime.now(timezone.utc)
    task.completed_by = uuid.UUID(user["sub"])
    await db.flush()
    await db.refresh(task)
    return task
