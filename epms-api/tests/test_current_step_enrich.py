"""enrich_current_step attaches the open approval task's role/approver/since
to in_review list items, sourced from the tasks mirror (not approval_step_idx)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text as sa_text
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


def test_pr_response_exposes_optional_current_step():
    from app.schemas.pr import PrResponse
    from app.schemas.current_step import CurrentStep
    field = PrResponse.model_fields["current_step"]
    assert field.default is None                    # optional, defaults null
    cs = CurrentStep.model_validate({
        "role": "gm_or_opm", "label": "GM / OPM",
        "approver_name": "Zhang San", "since": datetime.now(timezone.utc),
    })
    assert cs.label == "GM / OPM" and cs.approver_name == "Zhang San"


async def test_pr_list_endpoint_serializes_current_step_key(admin_client):
    r = await admin_client.get("/api/v1/pr")
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert "current_step" in item          # field present on every row


# ── I4 (2026-08-19 final review): stand-in lookup must batch, not N+1 ────────

async def test_enrich_current_step_batches_stand_in_lookup_no_n_plus_1(test_engine, monkeypatch):
    """enrich_current_step's own docstring promises 'one batched task query +
    one batched user-name query (no N+1)'. Before this fix it called
    `active_delegate_id` once per UNIQUE approver in the page (a query per
    approver, not per row) — this test seeds 3 documents with 3 DISTINCT
    approvers, each with an active delegation, and proves the per-approver
    helper is never invoked: the batched `active_delegate_ids` (plural) must
    be used instead, in a single call covering the whole set."""
    import app.crud.current_step as cs_module
    from app.core.delegation import local_today
    from datetime import timedelta

    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    docs = [uuid.uuid4() for _ in range(3)]
    approvers = [uuid.uuid4() for _ in range(3)]
    delegates = [uuid.uuid4() for _ in range(3)]
    today = local_today()

    async with sf() as db:
        for i, (doc_id, approver_id, delegate_id) in enumerate(zip(docs, approvers, delegates)):
            db.add(User(id=approver_id, email=f"appr{i}-{TAG}@x.com",
                        hashed_password=hash_password("x"), full_name=f"Approver {i}",
                        role="dept_manager", is_active=True))
            db.add(User(id=delegate_id, email=f"deleg{i}-{TAG}@x.com",
                        hashed_password=hash_password("x"), full_name=f"Delegate {i}",
                        role="dept_manager", is_active=True))
        await db.commit()

    async with sf() as db:
        for doc_id, approver_id in zip(docs, approvers):
            db.add(_task(doc_id, role="dept_manager", user_id=approver_id))
        for approver_id, delegate_id in zip(approvers, delegates):
            await db.execute(sa_text(
                "INSERT INTO approval_delegations "
                "(id, delegator_user_id, delegate_user_id, start_date, end_date, "
                " revoked_at, created_by) "
                "VALUES (:id, :delegator, :delegate, :start, :end, NULL, :delegator)"),
                {"id": uuid.uuid4(), "delegator": approver_id, "delegate": delegate_id,
                 "start": today - timedelta(days=1), "end": today + timedelta(days=1)})
        await db.commit()

        calls: list = []
        original_batch = cs_module.active_delegate_ids

        async def _spy_batch(db_, delegator_ids, today=None):
            calls.append(set(delegator_ids))
            return await original_batch(db_, delegator_ids, today)

        monkeypatch.setattr(cs_module, "active_delegate_ids", _spy_batch)
        # If a future edit ever regresses this back to the per-id helper, this
        # patch turns it into a hard failure rather than a silent perf regression.
        called_singular = {"n": 0}

        async def _forbid_singular(*a, **kw):
            called_singular["n"] += 1
            raise AssertionError("N+1 regression: active_delegate_id called per-approver")

        monkeypatch.setattr(cs_module, "active_delegate_id", _forbid_singular, raising=False)

        items = [SimpleNamespace(id=doc_id, status="in_review") for doc_id in docs]
        await enrich_current_step(db, "pr", items)

    assert called_singular["n"] == 0, "the per-approver N+1 helper was still called"
    assert len(calls) == 1, (
        f"active_delegate_ids (batched) should be called exactly ONCE for "
        f"the whole page, not once per approver — was called {len(calls)} times"
    )
    assert calls[0] == set(approvers), (
        "the single batched call did not cover every unique approver on the page"
    )

    names = {it.current_step["approver_name"] for it in items}
    assert names == {
        "Approver 0 (delegated: Delegate 0)",
        "Approver 1 (delegated: Delegate 1)",
        "Approver 2 (delegated: Delegate 2)",
    }
