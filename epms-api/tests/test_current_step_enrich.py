"""enrich_current_step attaches the open approval task's role/approver/since
to in_review list items, sourced from the tasks mirror (not approval_step_idx)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.crud.current_step import ROLE_LABELS, ROLE_ORDER, enrich_current_step
from app.models.task import Task
from app.models.user import User

TAG = uuid.uuid4().hex[:8]


def _task(doc_id, *, role, user_id=None):
    return Task(
        id=uuid.uuid4(), type="approve_pr", priority="normal",
        document_type="pr", document_id=doc_id, document_number="PR-X",
        assigned_role=role, assigned_user_id=user_id,
        title="Approve", is_completed=False,
    )


async def test_enrich_attaches_current_step_with_approver(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    approver = uuid.uuid4()
    async with sf() as db:
        db.add(User(id=approver, email=f"gm-{TAG}@x.com",
                    hashed_password=hash_password("x"), full_name="Zhang San",
                    role="gm_or_opm", is_active=True))
        await db.commit()
    async with sf() as db:
        db.add(_task(doc_id, role="gm_or_opm", user_id=approver))
        await db.commit()

        items = [SimpleNamespace(id=doc_id, status="in_review")]
        await enrich_current_step(db, "pr", items)

    cs = items[0].current_step
    assert cs is not None
    assert cs["role"] == "gm_or_opm"
    assert cs["label"] == "GM / OPM"
    assert cs["approver_name"] == "Zhang San"
    assert isinstance(cs["since"], datetime)


async def test_enrich_pool_task_has_null_approver_and_non_in_review_is_none(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pool_doc = uuid.uuid4()
    other_doc = uuid.uuid4()
    async with sf() as db:
        db.add(_task(pool_doc, role="finance_bp", user_id=None))  # role pool, no assignee
        await db.commit()

        items = [
            SimpleNamespace(id=pool_doc, status="in_review"),
            SimpleNamespace(id=other_doc, status="draft"),   # not in_review
            SimpleNamespace(id=uuid.uuid4(), status="in_review"),  # in_review, no task
        ]
        await enrich_current_step(db, "pr", items)

    assert items[0].current_step["label"] == "Finance BP"
    assert items[0].current_step["approver_name"] is None
    assert items[1].current_step is None   # not in_review
    assert items[2].current_step is None   # in_review but no open task


def test_role_maps_cover_default_workflow_roles():
    for role in ("supervisor", "dept_manager", "director", "gm_or_opm",
                 "procurement_manager", "finance_bp", "finance_manager"):
        assert role in ROLE_LABELS
        assert role in ROLE_ORDER
