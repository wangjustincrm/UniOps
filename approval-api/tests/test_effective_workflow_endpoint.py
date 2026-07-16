"""Tests for build_effective_workflow — the core logic backing GET workflow-steps.

No HTTP test harness exists in this service (tests are DB-level via engine_db_session).
We call build_effective_workflow directly, which is the only logic in the thin
GET /{doc_type}/{doc_id}/workflow-steps handler. This fully verifies the behaviour
the endpoint exposes.

Covers:
- Over-budget PR with fm_gm_opm mode → injected ob steps prepend the base workflow
- The returned step ids include both over-budget steps AND supervisor + director base steps
- Non-over-budget PR → no ob steps injected
- fm_only mode → only ob_finance_manager is prepended
"""
import uuid
from decimal import Decimal

import pytest

from app.crud.engine import build_effective_workflow
from app.models.config import CompanyConfig
from app.models.pr import PurchaseRequest
from app.models.user import User


# ── helpers ──────────────────────────────────────────────────────────────────


async def _seed_pr_and_cfg(db, *, over_budget: bool, over_budget_mode: str = "fm_gm_opm"):
    """Seed the minimum rows needed: a User (FK target), a PurchaseRequest, and a
    CompanyConfig with the given over_budget_mode.  Returns (pr, cfg)."""
    requester = User(id=uuid.uuid4(), role="requester", is_active=True)
    db.add(requester)
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-WF-{uuid.uuid4().hex[:4]}",
        title="Workflow steps test PR",
        status="draft",
        approval_step_idx=0,
        amount=Decimal("5000.00"),
        created_by=requester.id,
        over_budget=over_budget,
    )
    db.add(pr)
    await db.flush()

    # role_management/dept_* JSONB retired from this mirror (Task 4) — this test
    # only exercises build_effective_workflow, which reads workflow_defs +
    # budget_admin_config, so those fields never need seeding here.
    cfg = CompanyConfig(
        id=uuid.uuid4(),
        workflow_defs={},          # use defaults → [supervisor, dept_manager, director, gm_or_opm]
        budget_admin_config={"over_budget_mode": over_budget_mode},
    )
    db.add(cfg)
    await db.flush()

    return pr, cfg


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_over_budget_fm_gm_opm_prepends_two_ob_steps(engine_db_session):
    """Over-budget PR with fm_gm_opm mode: first two steps are ob_finance_manager and
    ob_gm_or_opm; supervisor and director base steps are also present downstream."""
    db = engine_db_session
    pr, cfg = await _seed_pr_and_cfg(db, over_budget=True, over_budget_mode="fm_gm_opm")

    steps = await build_effective_workflow(db, "pr", pr, cfg)
    ids = [s["id"] for s in steps]

    # Over-budget nodes come first
    assert ids[0] == "ob_finance_manager", f"Expected ob_finance_manager first, got {ids}"
    assert ids[1] == "ob_gm_or_opm", f"Expected ob_gm_or_opm second, got {ids}"

    # Base PR workflow nodes are present further down
    assert "supervisor" in ids, f"supervisor missing from {ids}"
    assert "director" in ids, f"director missing from {ids}"
    assert "dept_manager" in ids, f"dept_manager missing from {ids}"
    assert "gm_or_opm" in ids, f"gm_or_opm missing from {ids}"


@pytest.mark.asyncio
async def test_over_budget_fm_only_prepends_one_ob_step(engine_db_session):
    """fm_only mode: only ob_finance_manager is prepended, no ob_gm_or_opm."""
    db = engine_db_session
    pr, cfg = await _seed_pr_and_cfg(db, over_budget=True, over_budget_mode="fm_only")

    steps = await build_effective_workflow(db, "pr", pr, cfg)
    ids = [s["id"] for s in steps]

    assert ids[0] == "ob_finance_manager"
    assert "ob_gm_or_opm" not in ids, f"ob_gm_or_opm should not appear in fm_only mode: {ids}"
    assert "supervisor" in ids
    assert "director" in ids


@pytest.mark.asyncio
async def test_non_over_budget_pr_no_ob_steps(engine_db_session):
    """Non-over-budget PR: no ob_ steps; workflow is the plain default."""
    db = engine_db_session
    pr, cfg = await _seed_pr_and_cfg(db, over_budget=False)

    steps = await build_effective_workflow(db, "pr", pr, cfg)
    ids = [s["id"] for s in steps]

    ob_ids = [i for i in ids if i.startswith("ob_")]
    assert ob_ids == [], f"Expected no ob_ steps for non-over-budget PR, got {ob_ids}"

    # Default PR workflow intact
    assert "supervisor" in ids
    assert "dept_manager" in ids
    assert "director" in ids
    assert "gm_or_opm" in ids


@pytest.mark.asyncio
async def test_hard_block_mode_no_ob_steps_injected(engine_db_session):
    """hard_block mode: the engine does NOT prepend ob_ steps (submission is blocked
    instead); workflow-steps should return the plain base workflow."""
    db = engine_db_session
    pr, cfg = await _seed_pr_and_cfg(db, over_budget=True, over_budget_mode="hard_block")

    steps = await build_effective_workflow(db, "pr", pr, cfg)
    ids = [s["id"] for s in steps]

    ob_ids = [i for i in ids if i.startswith("ob_")]
    assert ob_ids == [], f"Expected no ob_ steps for hard_block mode, got {ob_ids}"
    # Base workflow is still returned
    assert "supervisor" in ids
    assert "director" in ids


@pytest.mark.asyncio
async def test_step_structure_has_required_keys(engine_db_session):
    """Every step dict must have id, role, and label keys."""
    db = engine_db_session
    pr, cfg = await _seed_pr_and_cfg(db, over_budget=True, over_budget_mode="fm_gm_opm")

    steps = await build_effective_workflow(db, "pr", pr, cfg)

    for step in steps:
        assert "id" in step, f"Missing 'id' in step: {step}"
        assert "role" in step, f"Missing 'role' in step: {step}"
        assert "label" in step, f"Missing 'label' in step: {step}"
