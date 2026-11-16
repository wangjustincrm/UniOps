"""Carry in-flight documents over to an edited approval workflow.

A document stores only WHERE it is in its chain — an integer, approval_step_idx —
and every approval event records the integer of the step it was given at. The
chain itself is read fresh from company_config.workflow_defs on every call. So
editing a chain used to silently re-point every in-flight document: the stored
integer now named a different step. On 2026-11-16 the Direct PA chain had its
Finance Manager and Finance BP steps swapped; 27 PAs waiting on the Finance
Manager were suddenly "at" the Finance BP step while their task still sat with
the Finance Manager (who could no longer approve it), and the approval line of
every Direct PA showed each approver under the other one's step.

This module runs when a chain is saved, while the chain BEFORE the save is still
known. Knowing both chains is what makes it exact rather than a guess:

1. Pair the steps of the old chain with the steps of the new one (`match_nodes`):
   same node id and role first (a step moved up or down keeps its id), then by
   role in order. A step whose role was changed in place is a different step.
2. The steps of the old chain the document had already passed are done; their
   partners in the new chain are satisfied (`plan_realign`).
3. Company rules (2026-11-16):
   * a step the document already passed is never asked again, wherever it moved;
   * a step ADDED to the chain (no partner in the old one) at a point the
     document has already passed — some satisfied step comes after it — is not
     sent back for: it is waived;
   * a step that already existed but has not been given yet is still required,
     even if it moved in front of steps already given;
   * when every step of the new chain is satisfied, the document is approved.
4. The document lands on the first step that is neither satisfied nor waived
   nor conditionally skipped; its approve task is reissued only when that step
   is not the one the open task was for.
5. The events of the current approval run are renumbered onto the new chain, so
   every approval line (which pairs events with steps by step_idx) shows each
   approval under the step it was actually given at. Events of a step that no
   longer exists get step_idx -1: still in the history, on no step.

Nothing here commits. The endpoint commits, or rolls back for a preview.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import engine as eng
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.expense import ExpenseClaim
from app.models.routing import WorkflowSnapshot
from app.models.task import Task

WAIVED_REASON = ("Auto-skipped (step added to the approval workflow after this "
                 "document had already passed it)")
REMOVED_STEP_IDX = -1

# ── Pure planning ────────────────────────────────────────────────────────────

def resolve_nodes(defs: dict | None, key: str) -> list[dict]:
    """The base chain `key` walks under `defs` — engine._get_workflow's
    fallback rules applied to an arbitrary workflow_defs dict, so the chain
    before a save can be resolved exactly as the engine resolved it."""
    defs = defs or {}
    nodes = defs.get(key) or []
    if nodes:
        return list(nodes)
    if key.startswith("cfm_"):
        nodes = defs.get("cfm") or []
        if nodes:
            return list(nodes)
    return list(eng._WORKFLOW_DEFAULTS.get(key)
                or eng._WORKFLOW_DEFAULTS.get("cfm" if key.startswith("cfm_") else key, []))


def match_nodes(old: list[dict], new: list[dict]) -> dict[int, int | None]:
    """old step index → its partner's index in `new` (None: the step is gone).

    Pass 1 pairs steps with the same id AND role — the editor keeps a step's id
    when it is moved, so a reorder pairs exactly even when a role occurs twice
    (a PR's over-budget GM/OPM step and its ordinary one). Pass 2 pairs what is
    left by role, in order. A step whose role changed in place pairs with
    nothing: it is a different approver."""
    mapping: dict[int, int | None] = {}
    taken: set[int] = set()
    for i, o in enumerate(old):
        for j, n in enumerate(new):
            if j not in taken and o.get("id") is not None and n.get("id") == o.get("id") \
                    and n.get("role") == o.get("role"):
                mapping[i] = j
                taken.add(j)
                break
    for i, o in enumerate(old):
        if i in mapping:
            continue
        mapping[i] = None
        for j, n in enumerate(new):
            if j not in taken and n.get("role") == o.get("role"):
                mapping[i] = j
                taken.add(j)
                break
    return mapping


@dataclass
class RealignPlan:
    node_map: dict[int, int | None]
    satisfied: set[int]            # new-chain steps already given
    waived: list[int]              # added steps the document has passed
    added: list[int] = field(default_factory=list)

    def passed(self, j: int) -> bool:
        return j in self.satisfied or j in self.waived


def plan_realign(old: list[dict], new: list[dict], done_old: set[int]) -> RealignPlan:
    """Which steps of `new` a document that had given the steps `done_old` of
    `old` has satisfied, and which added steps it is excused from (rules in the
    module docstring)."""
    node_map = match_nodes(old, new)
    satisfied = {node_map[i] for i in done_old
                 if 0 <= i < len(old) and node_map.get(i) is not None}
    added = [j for j in range(len(new)) if j not in node_map.values()]
    waived = [j for j in added if any(s > j for s in satisfied)]
    return RealignPlan(node_map=node_map, satisfied=satisfied, waived=waived, added=added)


def changed_keys(old_defs: dict | None, new_defs: dict | None, keys: set[str]) -> set[str]:
    """The chain keys among `keys` whose resolved chain differs. Label-only
    edits count as a change (cheap, and the task description carries the
    label) but realign then has nothing to move."""
    return {k for k in keys if resolve_nodes(old_defs, k) != resolve_nodes(new_defs, k)}


# ── Per-document realignment ─────────────────────────────────────────────────

def _label(node: dict | None) -> str:
    if node is None:
        return "—"
    return node.get("label") or node.get("role") or "?"


def _task_step(workflow: list[dict], task_role: str, prefer: int) -> int | None:
    """The step an open approve task belongs to: `prefer` (the stored index)
    when its role fits, else the first step whose role fits. Unlike
    engine._step_index_for_task_role this does not turn a plain gm / opm task
    into gm_or_opm — the Freight chain has plain GM and OPM steps."""
    if 0 <= prefer < len(workflow) and eng._event_role_fits(task_role, workflow[prefer]["role"]):
        return prefer
    return next((i for i, n in enumerate(workflow)
                 if eng._event_role_fits(task_role, n["role"])), None)


async def _old_position(db: AsyncSession, doc_type: str, ctx: eng.ActionContext,
                        old_wf: list[dict], status: str) -> int:
    """How far along the OLD chain the document was — the step it was waiting
    on (in review) or would resume at (returned). Same evidence the engine
    trusts: the open approve task's role first (resync's rule), else the stored
    index; for a returned document, the step its last return was taken at."""
    meta, doc = ctx.meta, ctx.doc
    if status == "returned":
        return await eng.resume_step(db, doc_type, doc, status, old_wf)
    stored = eng._step_of(meta, doc)
    open_tasks = (await db.execute(select(Task).where(
        Task.document_type == doc_type, Task.document_id == doc.id,
        Task.type == meta["task_approve"], Task.is_completed.is_(False),
    ))).scalars().all()
    idx = [_task_step(old_wf, t.assigned_role, stored) for t in open_tasks]
    idx = [i for i in idx if i is not None]
    if idx:
        return min(idx)
    return min(stored, len(old_wf))


async def realign_document(
    db: AsyncSession, doc_type: str, doc_id: uuid.UUID, old_defs: dict | None,
    actor_id: uuid.UUID,
) -> dict | None:
    """Carry one submitted / in-review / returned document from the chain it
    walked under `old_defs` onto the chain configured now. Returns what changed,
    or None when the document's chain did not change or nothing had to move."""
    ctx = await eng.load_action_context(db, doc_type, doc_id, for_update=True)
    meta, doc = ctx.meta, ctx.doc
    status = eng._status_of(meta, doc)
    if status not in ("submitted", "in_review", "returned"):
        return None

    key = eng.workflow_key(doc_type, doc)
    new_base = await eng._get_workflow(db, key)
    old_base = resolve_nodes(old_defs, key)
    if old_base == new_base:
        return None
    # The effective chain is the base chain behind any over-budget steps frozen
    # on a PR; those steps are not part of workflow_defs and stay as they are.
    new_wf = ctx.workflow if status != "returned" else ctx.submit_workflow
    prefix = new_wf[:len(new_wf) - len(new_base)]
    old_wf = prefix + old_base

    old_pos = await _old_position(db, doc_type, ctx, old_wf, status)
    run = await eng.current_run_events(db, doc_type, doc.id)
    # Done = every step before where it stood, plus any step further on already
    # approved in this run (carried over by an earlier workflow edit).
    done_old = set(range(min(old_pos, len(old_wf)))) | {
        e.step_idx for e in run
        if e.action == "approve" and 0 <= e.step_idx < len(old_wf)
        and eng._event_role_fits(e.actor_role, old_wf[e.step_idx]["role"])}
    plan = plan_realign(old_wf, new_wf, done_old)
    number = getattr(doc, meta["number_attr"])
    actions: list[str] = []

    # Renumber the current run's events onto the new chain.
    remapped = 0
    for e in run:
        if e.action not in ("approve", "return") or not 0 <= e.step_idx < len(old_wf):
            continue
        target = plan.node_map.get(e.step_idx)
        target = REMOVED_STEP_IDX if target is None else target
        if target != e.step_idx:
            e.step_idx = target
            remapped += 1
    if remapped:
        actions.append(f"renumber {remapped} event(s) onto the new chain")

    for j in plan.waived:
        db.add(ApprovalEvent(
            document_type=doc_type, document_id=doc.id, document_number=number,
            step_idx=j, action="approve", actor_id=actor_id,
            actor_role=new_wf[j]["role"], comment=WAIVED_REASON))
        actions.append(f"waive added step '{_label(new_wf[j])}'")

    old_node = old_wf[old_pos] if old_pos < len(old_wf) else None
    result = {"doc_type": doc_type, "doc_id": str(doc.id), "number": number,
              "workflow_key": key, "status": status, "from": _label(old_node),
              "completed": False}

    if status == "returned":
        # The requester resubmits; the engine resumes at the step the last
        # return was taken at. Point it at the first step still owed. All
        # owed steps satisfied → the last step, so the resubmission is seen
        # by someone (a returned document cannot approve itself).
        resume = next((j for j in range(len(new_wf)) if not plan.passed(j)),
                      max(len(new_wf) - 1, 0))
        last_return = next((e for e in reversed(run) if e.action == "return"), None)
        if last_return is not None and last_return.step_idx != resume:
            last_return.step_idx = resume
            actions.append(f"resume at '{_label(new_wf[resume])}' on resubmission")
        result["to"] = _label(new_wf[resume]) if new_wf else "—"
        await db.flush()
        if not actions:
            return None
        result["actions"] = actions
        return result

    # Submitted / in review: walk to the first step still owed.
    claim_ctx = ctx if ctx.meta["model"] is ExpenseClaim else None
    j = 0
    while j < len(new_wf):
        if plan.passed(j):
            j += 1
            continue
        skip, reason = eng._step_skip(
            new_wf[j], doc_type, doc, ctx.director_uid, ctx.supervisor_uid,
            ctx.dept_has_director, ctx.dept_has_supervisor, ctx.cross_dept_pa,
            ctx.cfg_ob_mode or None)
        if not skip and claim_ctx is not None:
            reason = await eng._claimant_step_skip(db, doc_type, claim_ctx, new_wf[j])
            skip = reason is not None
        if not skip:
            break
        db.add(ApprovalEvent(
            document_type=doc_type, document_id=doc.id, document_number=number,
            step_idx=j, action="approve", actor_id=actor_id,
            actor_role=new_wf[j]["role"], comment=f"{reason} — workflow changed"))
        actions.append(f"skip '{_label(new_wf[j])}' ({reason})")
        j += 1

    open_tasks = (await db.execute(select(Task).where(
        Task.document_type == doc_type, Task.document_id == doc.id,
        Task.type == meta["task_approve"], Task.is_completed.is_(False),
    ))).scalars().all()

    if j >= len(new_wf):
        # Every step of the new chain has been given (company rule 3).
        await eng._complete_tasks(db, doc_type, doc.id)
        eng._set_step(meta, doc, len(new_wf))
        eng._set_status(meta, doc, "approved")
        if hasattr(doc, "approved_at"):
            doc.approved_at = datetime.now(timezone.utc)
        post_fn = eng._POST_APPROVE.get(doc_type) or (
            eng._post_approve_exp if doc_type.startswith("cfm_") else None)
        if post_fn:
            await post_fn(db, doc)
        actions.append("approved — every step of the new chain already given")
        result.update(to="Approved", completed=True)
    else:
        same_step = (old_pos < len(old_wf) and plan.node_map.get(old_pos) == j)
        task_fits = bool(open_tasks) and all(
            eng._event_role_fits(t.assigned_role, new_wf[j]["role"]) for t in open_tasks)
        if eng._step_of(meta, doc) != j:
            actions.append(f"step {eng._step_of(meta, doc)} -> {j}")
            eng._set_step(meta, doc, j)
        if not (same_step and task_fits):
            await eng._complete_tasks(db, doc_type, doc.id)
            eng._set_status(meta, doc, "in_review")
            await eng._create_approve_task(
                db, doc_type, doc, step=j, workflow=new_wf, meta=meta, rm=ctx.rm,
                dept_gm_opm=ctx.dept_gm_opm, routing_dept_id=ctx.routing_dept_id,
                director_uid=ctx.director_uid, supervisor_uid=ctx.supervisor_uid,
                cross_dept_pa=ctx.cross_dept_pa)
            actions.append(f"approval task -> '{_label(new_wf[j])}'")
        result["to"] = _label(new_wf[j])

    await db.flush()
    if not actions:
        return None
    result["actions"] = actions
    return result


async def realign_after_workflow_change(
    db: AsyncSession, actor_id: uuid.UUID, *, new_defs: dict | None = None,
) -> dict:
    """Carry every in-flight document from the chains it is aligned to (the
    snapshot) onto the chains configured now, then move the snapshot forward.

    With `new_defs`, those are written into this session's company_config first
    — a preview, which the caller rolls back (the snapshot write with it).

    Each document runs in its own SAVEPOINT: one that cannot be routed (say its
    department has no manager) is reported in `errors` and left where it was.
    The snapshot moves forward regardless — the documents that did move are on
    the new chains now, and realigning them again from the old ones would move
    them twice. The ones reported need a hand (DM → Edit approval state).
    """
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    snap = (await db.execute(
        select(WorkflowSnapshot).where(WorkflowSnapshot.id == 1).with_for_update()
    )).scalar_one_or_none()
    if new_defs is not None and cfg is not None:
        cfg.workflow_defs = new_defs
        await db.flush()
    current = copy.deepcopy((cfg.workflow_defs if cfg else None) or {})
    out: dict[str, Any] = {"changed_workflows": [], "realigned": [], "errors": []}
    if snap is None:
        # First run ever (the migration seeds it, so only a fresh database):
        # nothing is known about earlier chains — start aligned to these.
        db.add(WorkflowSnapshot(id=1, workflow_defs=current, updated_by=actor_id))
        await db.flush()
        return out
    old_defs = snap.workflow_defs or {}

    keys = set(old_defs) | set(current) | set(eng._WORKFLOW_DEFAULTS)
    changed = changed_keys(old_defs, current, keys)
    out["changed_workflows"] = sorted(changed)
    if changed:
        rows = (await db.execute(
            select(Task.document_type, Task.document_id)
            .where(Task.is_completed.is_(False),
                   # PO sign-off's step task is `sign_po`, deliberately not approve_*
                   # (engine._DOC_META["posign"]); its revise task is revise_po_signoff.
                   or_(Task.type.like("approve%"), Task.type.like("revise%"),
                       Task.type == "sign_po"))
            .distinct()
        )).all()
        for doc_type, doc_id in rows:
            try:
                eng._resolve_meta(doc_type)
            except KeyError:
                continue
            try:
                async with db.begin_nested():
                    summary = await realign_document(db, doc_type, doc_id, old_defs, actor_id)
                if summary:
                    out["realigned"].append(summary)
            except LookupError:
                continue
            except Exception as exc:  # noqa: BLE001 — report it, keep the batch going
                out["errors"].append({"doc_type": doc_type, "doc_id": str(doc_id),
                                      "error": f"{type(exc).__name__}: {exc}"})
        out["realigned"].sort(key=lambda r: (r["workflow_key"], r["number"]))

    if snap.workflow_defs != current:
        snap.workflow_defs = current
        snap.updated_by = actor_id
        snap.updated_at = datetime.now(timezone.utc)
        await db.flush()
    return out
