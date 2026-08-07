"""agr action key — the Purchase Agreement approval chain."""
import pytest

from app.crud.engine import _DOC_META, _WORKFLOW_DEFAULTS, _resolve_meta

pytestmark = pytest.mark.asyncio


def test_agr_is_a_known_action_key():
    meta = _resolve_meta("agr")
    assert meta["model"].__tablename__ == "purchase_agreements"
    assert meta["number_attr"] == "number"
    assert meta["task_approve"] == "approve_agr"
    assert meta["task_revise"] == "revise_agr"
    assert "draft" in meta["valid_submit"]


def test_agr_has_seeded_default_workflow():
    steps = _WORKFLOW_DEFAULTS["agr"]
    assert [s["role"] for s in steps] == [
        "dept_manager", "procurement_manager", "finance_manager"]
    assert all("id" in s and "label" in s for s in steps)


def test_agr_amount_attr_points_at_a_real_column():
    # NTE is nullable; the engine must not blow up on an agreement without one.
    meta = _DOC_META["agr"]
    assert hasattr(meta["model"], meta["amount_attr"])
