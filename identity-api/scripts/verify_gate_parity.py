"""Phase-2 acceptance gate: prove the new require_permission() reads admit
EXACTLY who the old require_roles(...) gates admitted -- zero behaviour
change across all 57 replaced call sites' 12 permission keys.

For every (role x one of the 12 phase-2 keys), compares two INDEPENDENT
readings:

  OLD -- `role_code in PHASE2_DEFAULTS[key]`. PHASE2_DEFAULTS below is a
         frozen, hand-typed copy of the admission set each retired
         require_roles(...) tuple encoded (the same 12 tuples Task 2's
         seed script -- identity-api/scripts/seed_phase2_keys.py --
         wrote into role_permissions). It is deliberately NOT imported
         from that module: importing it would make both sides read the
         same dict, and a script that compares a dict to itself always
         says "OK" -- that proves nothing. Every tuple includes
         system_admin because every retired gate began
         `if role == "system_admin": return payload` (a short-circuit
         admit) -- even tuples whose literal source code omitted it,
         e.g. budget's _OPENING_WRITE_ROLES.
  NEW -- direct SQL read of role_permissions UNION role_permission_locks
         for that (role_code, permission_key) pair (the "effective
         matrix" -- a lock is a forced grant the UI can't clear, so it
         counts as admitted same as an explicit grant).

Any divergence prints `DIFF role=<code> key=<key> old=<bool> new=<bool>`
and the script exits nonzero -- that would mean Task 2's seed and the old
gates disagree, i.e. the migration changed who can do what. Full
agreement prints `GATE PARITY OK (<n> roles x 12 keys)` and exits 0.

Roles come from role_defs (17 built-in + any custom) -- not just the
roles named in PHASE2_DEFAULTS, so a custom role that was never granted
any of these keys also gets checked (old=False, new must also be False).

This is a one-shot acceptance tool for the phase-2 cutover, not a unit
test -- it has no test double of the database; its correctness is
demonstrated by running it against the real seeded dev DB and getting
GATE PARITY OK. It is read-only and safe to re-run any time -- but ONLY
UNTIL the first intentional matrix edit. PHASE2_DEFAULTS above is a frozen
snapshot of the admission sets at cutover; once an admin edits any of
these 12 keys via Portal -> Access Control (e.g. grants finance.coa.manage
to a new role), this script will legitimately print PARITY FAILED /
DIFF lines for that role x key -- that is the intended, correct
consequence of a deliberate matrix change, NOT a regression. Do not
reflexively treat a post-cutover DIFF here as a bug to roll back; first
check whether it lines up with a real Access Control edit.

Run inside the identity container:
    docker exec uniops_identity_api python -m scripts.verify_gate_parity
"""
import asyncio
import sys

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

# —— OLD: independently re-typed copy of the 12 retired gates' admission
# sets (NOT imported from scripts/seed_phase2_keys.py -- see module
# docstring for why that would make this check meaningless). Every tuple
# includes "system_admin": every require_roles(...) it replaces began
# `if role == "system_admin": return payload`. ——
PHASE2_DEFAULTS: dict[str, tuple[str, ...]] = {
    "epms.invoice.match":   ("system_admin", "ap_clerk", "finance_manager", "finance_bp"),
    "epms.po.write":        ("system_admin", "procurement_officer", "procurement_manager"),
    "epms.pa.write":        ("system_admin", "finance_bp", "finance_manager", "ap_clerk", "requester"),
    "epms.gr.receive":      ("system_admin", "warehouse_staff", "procurement_officer"),
    # NOTE: this is the phase-2 TARGET set (matrix-decides). coa.py still carries
    # a finance_bp-via-assignment branch (phase-3 legacy) that Task 5 removes; until
    # Task 5 runs, the running code admits one more path than this tuple. Parity here
    # is against the post-Task-5 target, by design (user decision: COA access is
    # decided in the Access Control matrix, not hardcoded). A finance.coa.manage DIFF
    # AFTER Task 5 lands would be a real regression.
    "finance.coa.manage":   ("system_admin", "finance_manager"),
    "finance.period.close": ("system_admin", "finance_manager"),
    "finance.jv.post":      ("system_admin", "finance_manager", "finance_bp"),
    "budget.catalog.write": ("system_admin", "finance_manager", "finance_bp"),
    "budget.plan.write":    ("system_admin", "finance_manager", "finance_bp", "dept_manager"),
    "budget.opening.write": ("system_admin", "finance_manager", "finance_bp"),
    "mdm.finance.write":    ("system_admin", "finance_manager", "ap_clerk"),
    "mdm.vendor.write":     ("system_admin", "vendor_manager", "finance_manager"),
}

PHASE2_KEYS = tuple(PHASE2_DEFAULTS)  # the 12 new permission keys, in order


async def _new_effective_matrix(session) -> dict[str, set[str]]:
    """NEW: role_code -> {permission_key}, direct read of
    role_permissions UNION role_permission_locks (identity's real
    effective-matrix definition -- a lock is a forced grant)."""
    rows = (await session.execute(sa.text(
        "SELECT role_code, permission_key FROM role_permissions "
        "WHERE permission_key = ANY(:keys) "
        "UNION "
        "SELECT role_code, permission_key FROM role_permission_locks "
        "WHERE permission_key = ANY(:keys)"),
        {"keys": list(PHASE2_KEYS)})).all()
    out: dict[str, set[str]] = {}
    for role_code, key in rows:
        out.setdefault(role_code, set()).add(key)
    return out


async def main() -> int:
    async with AsyncSessionLocal() as session:
        roles = (await session.execute(sa.text(
            "SELECT code FROM role_defs ORDER BY sort, code"))).scalars().all()
        new_matrix = await _new_effective_matrix(session)

        diffs: list[tuple[str, str, bool, bool]] = []
        for role in roles:
            for key in PHASE2_KEYS:
                old_val = role in PHASE2_DEFAULTS[key]
                new_val = key in new_matrix.get(role, set())
                if old_val != new_val:
                    diffs.append((role, key, old_val, new_val))

        for role, key, old_val, new_val in diffs:
            print(f"DIFF role={role} key={key} old={old_val} new={new_val}")

        if diffs:
            print(f"PARITY FAILED: {len(diffs)} divergence(s) across "
                  f"{len(roles)} roles x {len(PHASE2_KEYS)} keys", file=sys.stderr)
            return 1

        print(f"GATE PARITY OK ({len(roles)} roles x {len(PHASE2_KEYS)} keys)")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
