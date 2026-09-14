"""Field-level submit gates — the half approval-api does not own.

The split is deliberate and predates this file: approval-api owns the state
machine and never validates document fields, so "a PR needs a vendor" has to
live here, next to the model that has the field. See the comment on
pr.py::pr_action.

A PR has three of these, and they are exactly the kind of thing worth reporting
together: a requester who adds the vendor, resubmits, is told about the budget
account, fixes that, resubmits, and is then told about the completion date has
made three round trips to learn three things we knew at the start.

Gates are pure functions of the document so the submit route and the preflight
endpoint can share them: submission raises on the first failure (unchanged),
preflight collects them all.
"""
from dataclasses import dataclass

from app.schemas.gr import TYPE_FIXED_ASSET, TYPE_PROJECT, is_service

MSG_PR_VENDOR_REQUIRED = "A vendor is required before submitting this PR"
MSG_PR_BUDGET_REQUIRED = (
    "A cost center and budget code are required before submitting this PR"
)
MSG_PR_COMPLETION_DATE_REQUIRED = (
    "A Service/Project Expected Completion Date is required before submitting this PR"
)
MSG_PR_FIXED_ASSET_REQUIRED = (
    "A Fixed Asset ID is required before submitting this PR"
)
MSG_PR_PROJECT_CODE_REQUIRED = (
    "A Project No. is required before submitting this PR"
)


@dataclass
class Check:
    """One gate, plus what the person reading it can do about it.

    `fix_route` is where the fix happens, so a caller can offer the link instead
    of making someone hunt for the field. None when the gate already passes, or
    when there is nothing this user could open — see `fixable_by_user`.
    """
    id: str
    layer: str  # field  (approval-api reports rule | config)
    passed: bool
    message: str | None = None
    fixable_by_user: bool = True
    owner: str | None = None
    fix_route: str | None = None


def pr_submit_checks(pr) -> list[Check]:
    """Field gates this PR must clear before it can be submitted.

    Which gates apply depends on the PR: type 1 carries no budget, and only
    service/project PRs have a completion date. A gate that does not apply is
    omitted rather than reported as passed — the list is meant to read as "what
    this document has to satisfy", not "every rule we know of".
    """
    checks: list[Check] = []
    edit = f"/pr/{pr.id}/edit"

    has_vendor = pr.vendor_id is not None
    checks.append(Check(
        id="vendor_required", layer="field", passed=has_vendor,
        message=None if has_vendor else MSG_PR_VENDOR_REQUIRED,
        fix_route=None if has_vendor else f"{edit}#vendor",
    ))

    # Type 1 carries no budget — the Create PR form hides the Budget Account
    # block for it, so the gate genuinely does not apply rather than passing.
    # Why it is enforced at all: without BOTH fields compute_budget_check
    # short-circuits to over_budget=False, so an unbudgeted PR would silently
    # bypass the entire budget check, `hard_block` included. Only submit is
    # gated — legacy PRs with no budget code must stay approvable / cancellable.
    if pr.type != 1:
        has_budget = bool(pr.budget_code) and pr.cost_center_id is not None
        checks.append(Check(
            id="budget_account_required", layer="field", passed=has_budget,
            message=None if has_budget else MSG_PR_BUDGET_REQUIRED,
            fix_route=None if has_budget else f"{edit}#budget",
        ))

    # Only service/project PRs have a completion date, and without it
    # app/tasks/service_gr_due.py has no signal for "this should be finished by
    # now, go create a GR" — so the PR silently opts out of that reminder. The
    # create form has shown the field with a required asterisk since it was
    # written, but its zod rule was .optional() and neither payload carried the
    # value, which is why the server holds the authoritative copy.
    if is_service(pr.type):
        has_date = pr.service_completion_date is not None
        checks.append(Check(
            id="service_completion_date_required", layer="field", passed=has_date,
            message=None if has_date else MSG_PR_COMPLETION_DATE_REQUIRED,
            fix_route=None if has_date else f"{edit}#completion-date",
        ))

    # Type 5 and type 6 each have one identifier of their own, and both were in
    # the same state the completion date used to be in: the create form rendered
    # them with a required asterisk, the zod rule was .optional(), and nothing
    # server-side checked. The fixed asset id had it worse — it was never in the
    # payload at all, so every value typed into that box was discarded. PRD §2
    # has listed both as requirements since the types were defined.
    if pr.type == TYPE_FIXED_ASSET:
        has_asset = bool((pr.fixed_asset_id or "").strip())
        checks.append(Check(
            id="fixed_asset_id_required", layer="field", passed=has_asset,
            message=None if has_asset else MSG_PR_FIXED_ASSET_REQUIRED,
            fix_route=None if has_asset else f"{edit}#fixed-asset-id",
        ))

    if pr.type == TYPE_PROJECT:
        has_project = bool((pr.project_code or "").strip())
        checks.append(Check(
            id="project_code_required", layer="field", passed=has_project,
            message=None if has_project else MSG_PR_PROJECT_CODE_REQUIRED,
            fix_route=None if has_project else f"{edit}#project-code",
        ))

    return checks


# doc_type -> the field gates for its submit action. A doc_type absent here has
# no field gates of its own, which is not the same as having none checked:
# approval-api's state machine, budget and routing gates still apply.
SUBMIT_CHECKS = {
    "pr": pr_submit_checks,
}


def field_checks_for(doc_type: str, doc) -> list[Check]:
    fn = SUBMIT_CHECKS.get(doc_type)
    return fn(doc) if fn else []
