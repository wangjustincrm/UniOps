"""Backfill MISSING reconstructed approve events on imported docs.

A batch of imported PR/PO/PA carry only a [reconstructed] *submit* event (or a
partial set of approves): reconstruction ran when their status implied 0 (or
fewer) completed steps, then an incremental sync flipped the status to approved
without regenerating events (reconstruction is idempotent — it skips any doc
that already has an event). The doc now renders every node as a green check
(status = approved) but with NO name, because there is no approve event at that
step.

This fills ONLY the gaps: for every COMPLETED step of an imported doc that has no
event yet, it creates one — an approve attributed to the CURRENT holder (resolved
the same way the live engine does: named posts from users.role ∪ user_roles,
gm_or_opm via approval_dept_routing, dept_manager by the routing dept), or an
Auto-skipped event for an optional director/supervisor step. Existing events are
left untouched (re-attribution of stale-but-present actors is repair_reconstructed
_actors' job). Idempotent. Dry-run by DEFAULT; --apply commits.

Routing dept follows the originating PR requester (po->pr, pa->po->pr), like the
live engine. Run AFTER repair_approval_timeline (step alignment) so existing
events sit at the right index before gaps are computed.

Run (dev):
  docker compose ... exec epms-api python -m scripts.import_pms.backfill_missing_approvals
  docker compose ... exec epms-api python -m scripts.import_pms.backfill_missing_approvals --apply
"""
import argparse
import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import text, select

import app.db.session as sm
from app.models.approval import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder

_RECON = "[reconstructed]"
_MODELS = {"po": PurchaseOrder, "pa": PaymentApplication}
_SKIP_ROLES = {"director", "supervisor"}
# PO / PA only. PR is deliberately OUT OF SCOPE: its gm_or_opm is a conditional
# (amount-gated) step — verified 74/6233 approved PRs actually went through it — so
# a missing PR gm_or_opm event is legitimate, not a gap. PO/PA steps are all
# mandatory (≈100% coverage on approved docs), so their missing events are real gaps.
_FULLY_APPROVED = {
    "pr": {"approved", "paid", "closed"},
    "po": {"approved", "issued", "partially_received", "fully_received", "closed"},
    "pa": {"approved", "processed"},
}
SUE_EMAIL = "zhengsue@canadaroyalmilk.com"


def _u(v):
    try:
        return uuid.UUID(str(v)) if v else None
    except (ValueError, TypeError):
        return None


class _Holders:
    """Resolve the CURRENT active holder of a workflow role, mirroring the live
    engine's sources (identity user_roles + approval_dept_routing)."""

    async def prime(self, db):
        self.db = db
        self.wf = {}
        cfg = (await db.execute(text("SELECT workflow_defs FROM company_config LIMIT 1"))).scalar_one_or_none() or {}
        for dt in _MODELS:
            self.wf[dt] = [n["role"] for n in (cfg.get(dt) or [])]
        # dept -> 'gm'|'opm'
        self.dept_gm_opm = {d: g for d, g in (await db.execute(
            text("SELECT dept_id::text, gm_or_opm FROM approval_dept_routing"))).all()}
        # active dept_manager per dept (primary role)
        self.dept_mgr = {}
        for dept, uid in (await db.execute(text(
            "SELECT department_id::text, id::text FROM users "
            "WHERE role='dept_manager' AND is_active ORDER BY id"))).all():
            if dept:
                self.dept_mgr.setdefault(dept, _u(uid))
        self.user_dept = {u: d for u, d in (await db.execute(
            text("SELECT id::text, department_id::text FROM users"))).all()}
        self.pr_creator = {p: c for p, c in (await db.execute(
            text("SELECT id::text, created_by::text FROM purchase_requests"))).all()}
        self.po_pr = {o: p for o, p in (await db.execute(
            text("SELECT id::text, pr_id::text FROM purchase_orders"))).all()}
        self.sue = _u((await db.execute(text(
            "SELECT id::text FROM users WHERE lower(email)=:e"), {"e": SUE_EMAIL})).scalar_one_or_none())
        self._post_cache: dict[str, uuid.UUID | None] = {}

    async def _post_holder(self, code: str) -> uuid.UUID | None:
        if code in self._post_cache:
            return self._post_cache[code]
        row = (await self.db.execute(text(
            "SELECT id::text FROM users WHERE role=:c AND is_active "
            "UNION SELECT ur.user_id::text FROM user_roles ur JOIN users u ON u.id=ur.user_id "
            "  WHERE ur.role_code=:c AND u.is_active ORDER BY 1 LIMIT 1"), {"c": code})).scalar_one_or_none()
        self._post_cache[code] = _u(row)
        return self._post_cache[code]

    def routing_dept(self, doc_type, doc_id, created_by):
        ru = created_by
        if doc_type == "po":
            pr = self.po_pr.get(doc_id)
            ru = self.pr_creator.get(pr, created_by) if pr else created_by
        elif doc_type == "pa":
            po_id = self._pa_po.get(doc_id)
            pr = self.po_pr.get(po_id) if po_id else None
            ru = self.pr_creator.get(pr, created_by) if pr else created_by
        return self.user_dept.get(ru)

    async def actor_for(self, role, dept):
        if role in _SKIP_ROLES:
            return None
        if role == "dept_manager":
            return self.dept_mgr.get(dept) or await self._post_holder("gm")
        if role == "gm_or_opm":
            post = self.dept_gm_opm.get(dept, "gm")
            return await self._post_holder(post) or await self._post_holder("gm")
        if role == "ap_clerk":
            return await self._post_holder("ap_clerk") or self.sue
        # named posts: procurement_manager / finance_manager / finance_bp / etc.
        return await self._post_holder(role)


async def _repair(db, apply: bool) -> int:
    h = _Holders()
    await h.prime(db)
    # pa -> po_id (needed for routing)
    h._pa_po = {p: o for p, o in (await db.execute(
        text("SELECT id::text, po_id::text FROM payment_applications"))).all()}

    created = 0
    per = {"approve": 0, "skip": 0}
    by_role: dict[str, int] = {}
    for dt, Model in _MODELS.items():
        roles = h.wf.get(dt) or []
        if not roles:
            continue
        ids = (await db.execute(text(
            "SELECT DISTINCT document_id::text FROM approval_events "
            "WHERE document_type=:dt AND comment LIKE :r"), {"dt": dt, "r": f"{_RECON}%"})).scalars().all()
        for sid in ids:
            doc = (await db.execute(select(Model).where(Model.id == uuid.UUID(sid)))).scalar_one_or_none()
            if doc is None:
                continue
            status = doc.status
            if status in _FULLY_APPROVED.get(dt, set()):
                n_done = len(roles)
            elif status == "in_review":
                n_done = max(0, min(doc.approval_step_idx or 0, len(roles)))
            else:
                continue  # submitted / draft / rejected / cancelled: nothing to backfill
            # steps that already have an approve event
            have = set((await db.execute(text(
                "SELECT step_idx FROM approval_events WHERE document_type=:dt "
                "AND document_id=:id AND action='approve'"), {"dt": dt, "id": sid})).scalars().all())
            dept = h.routing_dept(dt, sid, str(doc.created_by))
            number = getattr(doc, "number", None) or getattr(doc, "pa_number", "")
            created_at = doc.created_at
            for i in range(n_done):
                if i in have:
                    continue
                role = roles[i]
                if role in _SKIP_ROLES:
                    if apply:
                        db.add(ApprovalEvent(
                            document_type=dt, document_id=doc.id, document_number=number,
                            step_idx=i, action="approve", actor_id=doc.created_by, actor_role=role,
                            comment=f"{_RECON} Auto-skipped ({role} not in legacy system)",
                            created_at=created_at + timedelta(seconds=i + 1)))
                    per["skip"] += 1
                    by_role[f"{dt}:{role}(skip)"] = by_role.get(f"{dt}:{role}(skip)",0)+1
                    created += 1
                    continue
                actor = await h.actor_for(role, dept)
                if actor is None:
                    continue  # no holder to attribute to; leave the gap
                if apply:
                    db.add(ApprovalEvent(
                        document_type=dt, document_id=doc.id, document_number=number,
                        step_idx=i, action="approve", actor_id=actor, actor_role=role,
                        comment=_RECON, created_at=created_at + timedelta(seconds=i + 1)))
                per["approve"] += 1
                by_role[f"{dt}:{role}"] = by_role.get(f"{dt}:{role}",0)+1
                created += 1

    if apply:
        await db.commit()
    else:
        await db.rollback()
    print(f"{'APPLIED' if apply else 'DRY-RUN'}: "
          f"{'created' if apply else 'would create'} {created} event(s) {per}."
          + ("" if apply else " --apply to commit."))
    for k in sorted(by_role): print(f"   {k}: {by_role[k]}")
    return created


async def main(apply: bool):
    async with sm.AsyncSessionLocal() as db:
        await _repair(db, apply)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
