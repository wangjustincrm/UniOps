"""Why can't I … on this PO? — the PO's own gates, run for the Assistant.

"为什么我不能为 PO-576-2610-01 创建付款申请？", "为什么我不能提交这张 PO 的签署？",
"为什么 PO-800-2609-01 不能建收货单？" were all answered "I can only check purchase
requests, not POs" (ISS-0017): the gate route knew only a PR's submit.

Each check here calls what the action itself calls — the GR route's status
tuples and permission helpers, create_pa's open-prepayment query and receipt
gate, the sign-off page's own state builder — so the answer is what pressing
the button would say, not a description of it. Nothing is written.

What this does not evaluate is said in `not_checked`, so a passing list is not
read as "you can certainly do it": for a payment application the invoices you
would pick are not known yet, and their own rules (same vendor and currency,
due dates within the window, not on another PA) apply when you pick them.
"""
from __future__ import annotations

import uuid

from uniops_authz import has_permission

ACTIONS = ("create_pa", "create_gr", "submit_signoff")

# The PO page offers Create PA only in these statuses (epms/src/pages/po/
# PoDetailPage.tsx canCreatePa); create_pa itself has no status gate.
# tests/test_rule_registry.py holds the two together.
PA_PAGE_PO_STATUSES = ("issued", "partially_received", "fully_received")


def _check(id_: str, passed: bool, message: str | None = None, *,
           fixable_by_user: bool = False, owner: str | None = None) -> dict:
    return {"id": id_, "layer": "rule", "passed": passed,
            "message": None if passed else message,
            "fixable_by_user": fixable_by_user, "owner": None if passed else owner}


async def _create_gr(db, user: dict, po) -> tuple[list[dict], list[str]]:
    from app.api.v1.gr import PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES
    from app.crud import gr as gr_crud
    from app.crud.pr_owner import get_pr_owner_id
    from app.schemas.gr import is_service

    service = is_service(po.type)
    allowed = SERVICE_GR_PO_STATUSES if service else PHYSICAL_GR_PO_STATUSES
    actor = uuid.UUID(user["sub"])
    can_receive = await has_permission(db, actor, user.get("role", ""), "epms.gr.receive")
    may_confirm = False
    if service and po.pr_id:
        ids = {await gr_crud.get_pr_requester_id(db, po.pr_id),
               await get_pr_owner_id(db, po.pr_id)}
        may_confirm = actor in {i for i in ids if i}
    kind = "a service or project PO" if service else "a goods PO"
    checks = [
        _check("po_status_allows_a_receipt", po.status in allowed,
               f"This is {kind} in status '{po.status}'. A receipt can be created "
               f"on {kind} only while it is {' / '.join(allowed)}.",
               ),
        _check("you_may_record_it", can_receive or may_confirm,
               ("Only the PR's requester or Service/Project Owner, or someone "
                "with the warehouse receive permission, can confirm a service PO.")
               if service else
               "Only someone with the warehouse receive permission can record a "
               "goods receipt.", owner="the warehouse" if not service else None),
    ]
    return checks, []


async def _create_pa(db, user: dict, po) -> tuple[list[dict], list[str]]:
    from app.api.v1.pa import PA_INVOICE_DUE_WINDOW_DAYS, open_prepayment_pa_number
    from app.crud import po as po_crud

    has_invoice = await po_crud.has_any_invoice(db, po.id)
    # The PO page's own "has_unpaid_invoice": an invoice not yet paid and not
    # claimed by another live PA (crud.po.payable_invoice_po_ids).
    payable = po.id in await po_crud.payable_invoice_po_ids(db, [po.id])
    matched = await po_crud.po_has_three_way_matched_invoice(db, po.id)
    blocking = await open_prepayment_pa_number(db, [po.id])
    may_write = await has_permission(db, uuid.UUID(user["sub"]), user.get("role", ""),
                                     "epms.pa.write")
    checks = [
        _check("you_may_create_a_pa", may_write,
               "Creating a payment application needs the PA write permission "
               "(epms.pa.write); the PO page does not show Create PA without it."),
        _check("page_offers_create_pa", po.status in PA_PAGE_PO_STATUSES,
               f"The PO is '{po.status}'. The PO page offers Create PA only once "
               f"the order is issued, partially received or fully received."),
        _check("an_invoice_to_pay", bool(po.is_prepaid) or payable,
               ("No invoice has been recorded against this PO yet. A payment "
                "application pays invoices, so the vendor's invoice has to be "
                "uploaded and matched to the PO first.") if not has_invoice else
               ("Every invoice on this PO is already paid or already on another "
                "payment application that is not cancelled or rejected, so a new "
                "PA has nothing to pay."),
               owner="whoever uploads and matches invoices (AP)" if not has_invoice else None),
        _check("three_way_matched_invoice", matched,
               "No invoice on this PO is three-way matched (a matched invoice "
               "with a recorded goods receipt or service confirmation). A regular "
               "PA needs one — record the receipt first — unless someone with the "
               "receipt-override permission overrides it with a reason. A "
               "prepayment PA is exempt.", fixable_by_user=True),
        _check("no_open_prepayment", blocking is None,
               f"Prepayment PA {blocking} on this PO is still open; a regular PA "
               f"cannot be created until it is settled or cancelled."),
    ]
    return checks, ["the invoices you would select: same vendor and currency, due "
                    f"dates within {PA_INVOICE_DUE_WINDOW_DAYS} days of each other, "
                    "not already on another payment application"]


async def _submit_signoff(db, user: dict, po, token: str) -> tuple[list[dict], list[str]]:
    from app.api.v1 import po_signoff

    try:
        workflow = await po_signoff._workflow(po.id, token)
    except Exception:  # noqa: BLE001 — the engine down is a partial answer, not a 500
        return [_check("signoff_status", po.signoff_status in ("draft", "returned"),
                       f"The sign-off is already '{po.signoff_status}'.")], [
            "the sign-off workflow could not be read from the approval engine"]
    state = await po_signoff._state(db, po, workflow, uuid.UUID(user["sub"]))
    checks = [_check("signoff_status", po.signoff_status in ("draft", "returned"),
                     f"The sign-off is already '{po.signoff_status}', so there is "
                     f"nothing to submit.")]
    for i, blocker in enumerate(state["blockers"]):
        checks.append(_check(f"signoff_blocker_{i + 1}", False, blocker,
                             owner="a System Admin (workflow / post holders) or "
                                   "the signer (My Profile → My Signature)"))
    return checks, []


async def check(db, user: dict, po, action: str, token: str) -> dict:
    """{"allowed", "complete", "checks", "not_checked"} — the same shape as the
    PR preflight, so the same narrator explains it."""
    if action == "create_gr":
        checks, not_checked = await _create_gr(db, user, po)
    elif action == "create_pa":
        checks, not_checked = await _create_pa(db, user, po)
    elif action == "submit_signoff":
        checks, not_checked = await _submit_signoff(db, user, po, token)
    else:
        raise ValueError(action)
    return {"allowed": all(c["passed"] for c in checks),
            # `complete` false tells the narrator the list is partial.
            "complete": not any("could not be read" in n for n in not_checked),
            "checks": checks, "not_checked": not_checked}
