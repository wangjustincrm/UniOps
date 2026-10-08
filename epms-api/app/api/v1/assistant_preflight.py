"""The whole answer to "why can't I submit this", in one response.

Two services hold the gates. epms-api owns the document's field rules; the
approval engine owns the state machine, budget mode, and approver routing.
Neither half is the answer on its own, and a person who gets them one at a time
gets sent around twice — so this endpoint asks both and returns the merged list.

Read-only throughout: nothing here submits anything, and a caller can ask as
often as it likes.
"""
from fastapi import APIRouter, HTTPException

from app.core.access_scope import build_scope, is_pr_visible
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import pr as pr_crud
from app.services import approval_client
from app.services.doc_preflight import field_checks_for

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Only PR is wired up so far. Listing supported types explicitly (rather than
# trying and failing) keeps the 400 honest about what exists.
_SUPPORTED = {"pr"}

# Layer ordering for the response: fix your own fields first, then the rules,
# then the things only an admin can change. Matches the order someone would
# actually work through them.
_LAYER_ORDER = {"field": 0, "rule": 1, "config": 2}


@router.get("/preflight/{doc_type}/{doc_id}")
async def preflight(
    doc_type: str,
    doc_id: str,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
    action: str = "submit",
) -> dict:
    if doc_type not in _SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"preflight supports {', '.join(sorted(_SUPPORTED))}, not '{doc_type}'",
        )
    if action.lower() != "submit":
        raise HTTPException(
            status_code=400,
            detail=f"preflight currently supports action=submit, not '{action}'",
        )

    doc = await pr_crud.get_by_id(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="PR not found")

    # Same visibility rule as opening the document: being told why a PR you
    # cannot see is blocked would leak both its existence and its state.
    scope = await build_scope(db, user)
    if not await is_pr_visible(db, doc.id, scope):
        raise HTTPException(status_code=404, detail="PR not found")

    checks = [
        {"id": c.id, "layer": c.layer, "passed": c.passed, "message": c.message,
         "fixable_by_user": c.fixable_by_user, "owner": c.owner,
         "fix_route": c.fix_route}
        for c in field_checks_for(doc_type, doc)
    ]

    # The engine's half. Its gates are reported even when a field gate already
    # failed — an unconfigured department manager is worth knowing about before
    # you go fill in a field and come back.
    engine_reachable = True
    try:
        engine = await approval_client.get_preflight(doc_type, str(doc.id), "submit", token)
        for c in engine.get("checks", []):
            checks.append({**c, "fix_route": c.get("fix_route")})
    except LookupError:
        raise HTTPException(status_code=404, detail="PR not found in approval engine")
    except RuntimeError as exc:
        # Report the outage as a check rather than failing the request: the field
        # gates we already computed are still useful, and claiming "everything is
        # fine" because we could not ask would be worse than saying so.
        engine_reachable = False
        checks.append({
            "id": "approval_engine_reachable", "layer": "config", "passed": False,
            "message": str(exc), "fixable_by_user": False, "owner": "admin",
            "fix_route": None,
        })

    checks.sort(key=lambda c: (_LAYER_ORDER.get(c["layer"], 9), c["passed"]))
    return {
        "doc_type": doc_type,
        "doc_id": str(doc.id),
        "document_number": getattr(doc, "number", None),
        "action": "submit",
        # An unreachable engine means unknown, not allowed.
        "allowed": engine_reachable and all(c["passed"] for c in checks),
        "complete": engine_reachable,
        "checks": checks,
    }
