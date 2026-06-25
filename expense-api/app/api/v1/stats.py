"""Stats endpoint — used by Portal Home to show pending counts."""
from fastapi import APIRouter
from app.core.deps import CurrentUserDep, SessionDep
from app.crud.pa import count_pending as count_pending_pa
from app.crud.expense import count_pending as count_pending_exp

router = APIRouter(tags=["stats"])


@router.get("/api/v1/stats/pending")
async def stats_pending(db: SessionDep, _: CurrentUserDep):
    """Returns combined count of PAs + expense claims pending action."""
    pa_pending = await count_pending_pa()
    exp_pending = await count_pending_exp(db)
    return {"pending": pa_pending + exp_pending}
