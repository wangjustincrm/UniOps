"""Re-attribute reconstructed named-post events to the CURRENT active holder.

[reconstructed] approve events for the company-wide named posts were attributed
from the RETIRED company_config.role_management snapshot, frozen at import time.
When that snapshot names a user who is now deactivated (e.g. the test user
'PM test' left configured as procurement_manager / finance_bp), the events keep
showing the stale name — even though the live engine already resolves these
posts from Access Control (identity user_roles, active users only) and never
picks the disabled user.

This realigns the frozen events to the SAME source the live engine uses
(crud.workflow._post_holders): the ACTIVE holder from users.role ∪ user_roles
for the singleton posts, and from user_roles for finance_bp. Set the real holder
in Access Control -> User Roles FIRST; this reads whatever is assigned there.

Scope: procurement_manager, finance_manager, finance_bp — the named posts most
likely to carry a stale config holder. Dept-routed roles (dept_manager,
gm_or_opm) resolve per-department and are left to the live routing / the step
re-index repair; ap_clerk (HoldBy), skip events and the submit event are never
touched. Dry-run by DEFAULT; --apply commits.

Run (dev):
  docker compose ... exec epms-api python -m scripts.import_pms.repair_reconstructed_actors
  docker compose ... exec epms-api python -m scripts.import_pms.repair_reconstructed_actors --apply
"""
import argparse
import asyncio
import uuid

from sqlalchemy import text

import app.db.session as sm

_RECON = "[reconstructed]"
# post code -> whether users.role (primary) also counts as holding it. Mirrors
# crud.workflow._post_holders: singleton posts count primary+assignment;
# finance_bp is assignment-only (user_roles).
# For historical-display attribution we count a post as "held" by anyone who has
# it as PRIMARY role OR an additional user_roles assignment — i.e. what Access
# Control shows. NB: the live engine resolves finance_bp from user_roles ONLY (a
# primary-role finance_bp is NOT an approver until assigned); so a name shown here
# for finance_bp may not be able to approve a live finance_bp step until assigned.
_POSTS = {
    "procurement_manager": True,
    "finance_manager": True,
    "finance_bp": True,
}


async def _active_holder(db, code: str, include_primary: bool) -> uuid.UUID | None:
    """The active holder of a post, resolved exactly like the live engine
    (users.role UNION user_roles for singletons; user_roles only for finance_bp).
    Deterministic ORDER BY uid so a broken singleton invariant still picks the
    same holder the engine's get_role_management would."""
    if include_primary:
        sql = (
            "SELECT id::text AS uid FROM users WHERE role = :c AND is_active "
            "UNION "
            "SELECT ur.user_id::text FROM user_roles ur JOIN users u ON u.id = ur.user_id "
            "  WHERE ur.role_code = :c AND u.is_active "
            "ORDER BY uid LIMIT 1"
        )
    else:
        sql = (
            "SELECT ur.user_id::text AS uid FROM user_roles ur JOIN users u ON u.id = ur.user_id "
            "  WHERE ur.role_code = :c AND u.is_active ORDER BY uid LIMIT 1"
        )
    row = (await db.execute(text(sql), {"c": code})).scalar_one_or_none()
    return uuid.UUID(row) if row else None


async def _name(db, uid) -> str:
    if uid is None:
        return "—"
    n = (await db.execute(text("SELECT full_name FROM users WHERE id = :i"),
                          {"i": str(uid)})).scalar_one_or_none()
    return n or str(uid)


async def _repair(db, apply: bool) -> int:
    holders: dict[str, uuid.UUID | None] = {}
    print("Current ACTIVE holders (from Access Control / user_roles):")
    for code, primary in _POSTS.items():
        holders[code] = await _active_holder(db, code, primary)
        print(f"  {code}: {await _name(db, holders[code])}"
              + ("" if holders[code] else "  ⚠️ NO ACTIVE HOLDER — assign one in Access Control first"))
    print()

    changed = 0
    per_role: dict[str, int] = {}
    for code, tgt in holders.items():
        if tgt is None:
            continue  # nothing to point at; leave events untouched
        # Touch events whose frozen actor NO LONGER validly holds this post —
        # deactivated OR simply un-assigned from it in Access Control (the reported
        # case: PM test still active but no longer the procurement_manager). Events
        # already attributed to the current valid holder are left untouched, so a
        # legitimately-attributed active approver is never overwritten.
        ev_ids = (await db.execute(text(
            "SELECT e.id::text FROM approval_events e "
            "WHERE e.actor_role = :c AND e.action = 'approve' "
            "  AND e.comment LIKE :recon AND e.comment NOT LIKE '%Auto-skipped%' "
            "  AND e.actor_id <> :tgt "
            "  AND NOT EXISTS ("
            "     SELECT 1 FROM users u WHERE u.id = e.actor_id AND u.is_active "
            "       AND (u.role = :c OR u.id IN "
            "            (SELECT user_id FROM user_roles WHERE role_code = :c)))"),
            {"c": code, "recon": f"{_RECON}%", "tgt": str(tgt)})).scalars().all()
        if ev_ids:
            per_role[code] = len(ev_ids)
            print(f"  {code}: {len(ev_ids)} event(s) whose actor no longer holds the post -> {await _name(db, tgt)}")
            if apply:
                for i in range(0, len(ev_ids), 500):
                    await db.execute(text(
                        "UPDATE approval_events SET actor_id = :tgt WHERE id = ANY(:ids)"),
                        {"tgt": str(tgt), "ids": [uuid.UUID(x) for x in ev_ids[i:i + 500]]})
            changed += len(ev_ids)

    if apply:
        await db.commit()
    else:
        await db.rollback()
    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: "
          f"{'re-attributed' if apply else 'would re-attribute'} {changed} event(s) "
          f"{per_role or '{}'}." + ("" if apply else " --apply to commit."))
    return changed


async def main(apply: bool):
    async with sm.AsyncSessionLocal() as db:
        await _repair(db, apply)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
