"""CONDITIONAL_ROLES must describe exactly the steps _should_skip_step skips.

The two live next to each other for a reason: the stored workflow definition has
no notion of an optional step, so anything explaining the process to a person
reads the annotation instead. When they drift, the explanation stays confident
and becomes wrong — the assistant told a requester a PR needs both a department
manager and a director, when most departments have no director and that step
never runs for them.
"""
from app.crud.engine import CONDITIONAL_ROLES, _should_skip_step


class _Doc:
    """Stand-in document: nothing on it makes a step skip by itself."""
    quality_approver_id = None


def _skips(role, doc_type="pr", *, director=None, supervisor=None):
    skip, _ = _should_skip_step(role, doc_type, _Doc(), director, supervisor,
                                dept_has_director=False, dept_has_supervisor=False)
    return skip


def test_every_annotated_role_can_actually_be_skipped():
    """An annotation for a step that always runs would tell people to expect an
    approval they will in fact always get — harmless — but more likely means the
    rule was removed and the description was left behind."""
    assert _skips("director", director=None)
    assert _skips("supervisor", supervisor=None)
    assert _skips("quality_manager", doc_type="vms_visit")
    assert set(CONDITIONAL_ROLES) == {"director", "supervisor", "quality_manager"}


def test_a_role_that_always_runs_is_not_annotated():
    assert not _skips("dept_manager")
    assert "dept_manager" not in CONDITIONAL_ROLES
    assert not _skips("finance_manager")
    assert "finance_manager" not in CONDITIONAL_ROLES


def test_a_configured_holder_means_the_step_runs():
    """The condition is about configuration, not about the role existing."""
    import uuid
    assert not _skips("director", director=uuid.uuid4())
    assert not _skips("supervisor", supervisor=uuid.uuid4())


def test_each_condition_says_when_it_runs():
    """The text is read verbatim by whoever explains the process, so it has to
    be a sentence about when the step applies, not a label."""
    for role, text in CONDITIONAL_ROLES.items():
        assert text.startswith("Only runs when"), role
        assert text.endswith("."), role
