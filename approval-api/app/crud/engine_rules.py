"""How the engine behaves, in words — beside the code that behaves that way.

GET /workflows/rules serves these, and the EPMS Assistant answers "what happens
when…" questions from them (ISS-0018, ISS-0026, ISS-0027). It used to have
only the chain's steps, so it filled the gaps from general knowledge of how
approval systems usually work: a returned PR "starts again from the first
step", "the system does not merge two steps held by one person", "the System
Admin has no role in the chain". Each of those was the opposite of what this
module's neighbour, engine.py, does.

Every rule names the tests that hold the behaviour (`pinned_by`);
tests/test_engine_rules.py fails when a named test does not exist, so a rule
cannot be stated here without one. Facts that live in a constant are read from
that constant rather than retyped, and settings an admin can change (which
post approves a cross-department payment) are read when the rules are served.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.engine import _DOC_META, RECALLABLE_STATUSES
from app.crud.workflow import get_approval_setting
from app.models.routing import CROSS_DEPT_GM_OR_OPM

# The EPMS documents the Assistant describes, in the words a person uses.
_DOC_WORDS = {
    "pr": "a purchase request",
    "po": "a purchase order",
    "agr": "a purchase agreement",
    "posign": "a PO sign-off",
    "pa": "a payment application",
}
_POST_WORDS = {"gm": "the GM", "opm": "the OPM"}


def _statuses(values) -> str:
    values = list(values)
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " or " + values[-1]


def _cancel_statuses() -> str:
    """Per document, the statuses `cancel` accepts — _DOC_META's own tuples."""
    return "; ".join(f"{word}: {_statuses(_DOC_META[dt]['valid_cancel'])}"
                     for dt, word in _DOC_WORDS.items())


# id → (area, answer, pinned_by). `{name}` placeholders are filled by
# engine_rules() below; everything else is literal.
ENGINE_RULES: list[dict] = [
    {
        "id": "returned_document_resumes_at_the_returning_step",
        "area": "approval_engine",
        "answer": (
            "When a returned document is resubmitted, approval resumes at the step "
            "that returned it — approvals already given stand and are not repeated. "
            "It starts again from the first step only when the return cannot be "
            "traced (imported data), when that step no longer exists in the chain, "
            "or, for a purchase request, when the over-budget steps in front of the "
            "chain changed while it was returned. A recalled document is a draft "
            "again and always starts from the first step. This is the same for "
            "every document type."),
        "pinned_by": [
            "tests/test_engine_resume_after_return.py::test_resubmit_goes_back_to_the_step_that_returned_it",
            "tests/test_engine_resume_after_return.py::test_step_beyond_a_shortened_workflow_starts_over",
            "tests/test_engine_resume_after_return.py::test_recalled_document_starts_over",
        ],
    },
    {
        "id": "recall_returns_to_draft",
        "area": "approval_engine",
        "answer": (
            "The person who submitted a document — or a System Admin — can recall it "
            "while it is {recallable} (on a purchase request this is the Recall to "
            "Edit button). It goes back to draft, can be edited, and when submitted "
            "again it starts from the first step."),
        "pinned_by": [
            "tests/test_engine_recall.py::test_recall_by_submitter_succeeds",
            "tests/test_engine_recall.py::test_recall_by_non_submitter_rejected",
            "tests/test_engine_recall.py::test_recall_by_system_admin_allowed",
        ],
    },
    {
        "id": "same_approver_on_consecutive_steps",
        "area": "approval_engine",
        "answer": (
            "When someone approves a step and also holds the next step in their own "
            "right, the next step is approved automatically and recorded as "
            "\"Auto-approved (same approver holds both roles)\"; this repeats for "
            "every following step they hold. Three exceptions: it does not happen "
            "at submission — a requester who is also the first approver must still "
            "approve after submitting; it does not happen through a delegation — a "
            "delegate approves only the step they acted on; and it does not happen "
            "for a System Admin approving outside their own posts."),
        "pinned_by": [
            "tests/test_engine_same_approver_skip.py::test_second_step_auto_skips_for_non_first_post_holder",
            "tests/test_engine_same_approver_skip.py::test_second_step_does_not_skip_for_a_different_person",
            "tests/test_engine_rules.py::test_no_same_approver_skip_at_submission",
            "tests/test_engine_rules.py::test_no_same_approver_skip_through_a_delegation",
            "tests/test_engine_rules.py::test_a_system_admin_approval_does_not_cascade",
        ],
    },
    {
        "id": "system_admin_can_act_on_any_step",
        "area": "approval_engine",
        "answer": (
            "A System Admin (as their main role) can approve, return or reject any "
            "step of any chain, whoever it is waiting on. Approving that way "
            "completes only that step: the following steps are not approved "
            "automatically, and someone a System Admin delegated to inherits none "
            "of this."),
        "pinned_by": [
            "tests/test_engine_rules.py::test_a_system_admin_approval_does_not_cascade",
            "tests/test_engine_return_cancel_authz.py::test_return_by_system_admin_succeeds",
            "tests/test_delegation_authz.py::test_system_admin_is_never_inherited",
        ],
    },
    {
        "id": "who_can_approve_return_or_reject",
        "area": "approval_engine",
        "answer": (
            "Only the approver of the step the document is currently on — or "
            "someone they have delegated to, or a System Admin — can approve, "
            "return or reject it. Anyone else is refused, even if they can see "
            "the document or the task."),
        "pinned_by": [
            "tests/test_engine_return_cancel_authz.py::test_return_by_bystander_rejected",
            "tests/test_engine_return_cancel_authz.py::test_return_by_current_approver_succeeds",
            "tests/test_engine_multiholder_approve.py::test_reject_denied_for_non_approver",
            "tests/test_engine_multiholder_approve.py::test_reject_allowed_for_authorized_holder",
        ],
    },
    {
        "id": "return_versus_reject",
        "area": "approval_engine",
        "answer": (
            "Return sends the document back to the person who submitted it, with a "
            "revise task; they can change it and resubmit (see "
            "returned_document_resumes_at_the_returning_step). Reject ends it: the "
            "document becomes Rejected — a payment application becomes Cancelled — "
            "and cannot be resubmitted."),
        "pinned_by": [
            "tests/test_engine_return_cancel_authz.py::test_return_by_current_approver_succeeds",
            "tests/test_engine_multiholder_approve.py::test_reject_allowed_for_authorized_holder",
        ],
    },
    {
        "id": "who_can_cancel",
        "area": "approval_engine",
        "answer": (
            "Cancelling (Withdraw) is open to the document's owner, the approver "
            "it is currently waiting on, or a System Admin, and only in these "
            "statuses — {cancel_statuses}. In any other status it is refused."),
        "pinned_by": [
            "tests/test_engine_return_cancel_authz.py::test_cancel_by_bystander_rejected",
            "tests/test_engine_return_cancel_authz.py::test_cancel_by_owner_succeeds",
            "tests/test_engine_return_cancel_authz.py::test_cancel_by_current_approver_succeeds",
            "tests/test_engine_rules.py::test_cancel_statuses_are_the_engines",
        ],
    },
    {
        "id": "empty_post_waits",
        "area": "approval_engine",
        "answer": (
            "A step for a company post — GM, OPM, Finance Manager, Procurement "
            "Manager, Finance Director and the like — is never skipped because "
            "nobody holds the post. Its task is addressed to the post, not to a "
            "person, so the document waits at that step; as soon as someone is "
            "given the post they see the task and can approve it, with nothing "
            "re-sent."),
        "pinned_by": [
            "tests/test_engine_rules.py::test_an_empty_post_step_waits_and_its_new_holder_can_approve",
            "tests/test_engine_optional_levels.py::test_gm_or_opm_task_is_broadcast",
        ],
    },
    {
        "id": "department_steps",
        "area": "approval_engine",
        "answer": (
            "The Department Manager step needs an active Department Manager for the "
            "document's department: without one the document cannot be submitted "
            "(the error says so) and stays where it is until an admin assigns one. "
            "The Director step runs only when the department has an active Director "
            "configured, and the Supervisor step only when the requester has an "
            "active Supervisor in a department with that level switched on; "
            "otherwise they are skipped and the timeline records why."),
        "pinned_by": [
            "tests/test_engine_dept_manager_routing.py::test_submit_pr_without_dept_manager_raises_not_broadcast",
            "tests/test_engine_optional_levels.py::test_director_skipped_when_unmapped",
            "tests/test_engine_optional_levels.py::test_supervisor_skipped_at_submit",
            "tests/test_engine_optional_levels.py::test_inactive_director_skipped_with_reason",
        ],
    },
    {
        "id": "the_department_decides_the_approvers",
        "area": "approval_engine",
        "answer": (
            "The department selected on the purchase request decides who approves "
            "it at the Department Manager, Director and GM/OPM steps — and the same "
            "for the purchase orders and payment applications raised from it. If "
            "the PR has no department, the requester's own department is used. An "
            "agreement, and a payment application raised against one, use the "
            "department on the agreement."),
        "pinned_by": [
            "tests/test_engine_dept_manager_routing.py::test_pr_department_id_drives_dept_manager_routing",
            "tests/test_engine_dept_manager_routing.py::test_null_pr_department_falls_back_to_creator_department",
            "tests/test_engine_dept_manager_routing.py::test_agreement_pa_routes_on_the_agreements_department",
            "tests/test_engine_dept_manager_routing.py::test_po_backed_pa_still_routes_through_its_pr",
        ],
    },
    {
        "id": "cross_department_payment",
        "area": "approval_engine",
        "answer": (
            "A payment application whose purchase orders come from more than one "
            "department is approved differently: the Department Manager and "
            "Director steps are skipped (shown struck through on the timeline with "
            "the reason) and the GM/OPM step goes to {cross_dept_post} for every "
            "such payment — the Approval Routing setting for cross-department "
            "payments, GM unless an admin changes it. A payment for one "
            "department follows that department's chain."),
        "pinned_by": [
            "tests/test_engine_cross_department_pa.py::test_cross_department_pa_skips_manager_and_director",
            "tests/test_engine_cross_department_pa.py::test_cross_department_gm_opm_comes_from_the_setting_not_the_department",
            "tests/test_engine_cross_department_pa.py::test_cross_department_defaults_to_gm_when_unset",
            "tests/test_engine_cross_department_pa.py::test_single_department_pa_still_routes_by_department",
        ],
    },
    {
        "id": "backup_approvers_are_not_used",
        "area": "delegation",
        "answer": (
            "Approval Routing can record a GM backup and an OPM backup, but nothing "
            "uses them: a backup is not sent the task and cannot approve in the "
            "GM's or OPM's place. Approval Delegation is the only way to have "
            "someone cover an absent approver."),
        "pinned_by": [
            "tests/test_engine_rules.py::test_a_recorded_backup_cannot_approve",
        ],
    },
    {
        "id": "delegation_is_set_by_a_system_admin",
        "area": "delegation",
        "answer": (
            "Only a System Admin can create, change or revoke an approval "
            "delegation: who is covered, who covers, and the first and last day. "
            "Nobody can set up their own. A delegation covers whole days in plant "
            "time from its start date to its end date, stops at once when revoked, "
            "a person can have only one delegation covering any given day, nobody "
            "can delegate to themselves, and it has no effect while the delegate's "
            "account is inactive."),
        "pinned_by": [
            "tests/test_delegation_api.py::test_non_admin_gets_403_on_every_verb",
            "tests/test_delegation_api.py::test_post_rejects_self_delegation",
            "tests/test_delegation_api.py::test_post_overlapping_window_returns_409_with_readable_message",
            "tests/test_delegation_query.py::test_window_boundaries",
            "tests/test_delegation_query.py::test_revoked_is_ignored_immediately",
            "tests/test_delegation_query.py::test_inactive_delegate_is_ignored",
        ],
    },
    {
        "id": "delegate_decides_approval_steps",
        "area": "delegation",
        "answer": (
            "During the delegation the delegate can approve, return or reject the "
            "approval steps of the person they cover, as that person could; the "
            "history records the delegate's name \"on behalf of\" the person "
            "covered. The person covered keeps the right to act themselves."),
        "pinned_by": [
            "tests/test_delegation_authz.py::test_delegate_is_authorized_inside_the_window",
            "tests/test_delegation_authz.py::test_delegator_keeps_the_right",
            "tests/test_delegation_authz.py::test_approval_event_records_the_delegate_and_names_the_delegator",
            "tests/test_delegation_authz.py::test_reject_event_also_names_the_delegator",
            "tests/test_engine_rules.py::test_a_delegate_can_return",
        ],
    },
    {
        "id": "delegation_is_one_level",
        "area": "delegation",
        "answer": (
            "Delegation is one level only: a delegate cannot pass it on — the "
            "delegate's own delegate gains nothing from the first person — and "
            "someone covering a System Admin does not get the System Admin's "
            "right to approve any step."),
        "pinned_by": [
            "tests/test_delegation_query.py::test_not_transitive",
            "tests/test_delegation_authz.py::test_system_admin_is_never_inherited",
        ],
    },
]


async def engine_rules(db: AsyncSession) -> list[dict]:
    """The rules as served: placeholders filled from constants and settings."""
    post = await get_approval_setting(db, CROSS_DEPT_GM_OR_OPM, "gm")
    values = {
        "recallable": _statuses(RECALLABLE_STATUSES),
        "cancel_statuses": _cancel_statuses(),
        "cross_dept_post": _POST_WORDS.get(post, _POST_WORDS["gm"]),
    }
    return [{"id": r["id"], "area": r["area"], "answer": r["answer"].format(**values)}
            for r in ENGINE_RULES]
