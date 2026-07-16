"""One-shot acceptance verifier for the approval routing phase 3 migration.

For every active department x ("pr", "po", "pa"), resolves each workflow
step's role to a user id (or dept-level flag, for `supervisor`) two ways:

  OLD  -- direct read of company_config's four now-retired JSONB columns
          (role_management / dept_gm_opm_mapping / dept_director_mapping /
          dept_supervisor_enabled). This module keeps its own frozen copy
          of that parsing logic (see `_old_readings` below) because the
          approval-api ORM model (app/models/config.py) no longer carries
          those columns -- the physical columns are still there (epms-api
          owns them), which is exactly what makes this comparison possible.
  NEW  -- app.crud.workflow's real getters (get_role_management /
          get_dept_gm_opm_mapping / get_dept_director_mapping /
          get_dept_supervisor_enabled), sourced from identity user_roles +
          approval-api's own approval_dept_routing / approval_backups
          tables.

Any divergence prints a `DIFF dept=<code> doc=<type> step=<role>
old=<val> new=<val>` line and the script exits nonzero. Full agreement
prints `PARITY OK (N depts x 3 doc types)` and exits 0.

This is a one-shot acceptance tool for the migration cutover -- run it
after `migrate` + `seed_routing`, before declaring the release good. Safe
to delete once the release is verified (the four JSONB columns it reads
are a frozen snapshot; nothing keeps writing to them going forward).

Run inside the approval container:
    docker exec uniops_approval_api python -m scripts.verify_routing_parity
"""
import asyncio
import json
import sys

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal
from app.crud.workflow import (
    get_dept_director_mapping,
    get_dept_gm_opm_mapping,
    get_dept_supervisor_enabled,
    get_role_management,
    get_workflow,
)

# Post-holder roles whose user id is looked up directly off role_management.
_POST_ROLES = ("gm", "opm", "finance_manager", "procurement_manager", "vendor_manager")

# Doc types the migrated routing rules actually govern.
_DOC_TYPES = ("pr", "po", "pa")


def _as_dict(v) -> dict:
    return json.loads(v) if isinstance(v, str) else (v or {})


async def _old_readings(session) -> tuple[dict, dict, dict, dict]:
    """OLD口径: company_config 四件套 JSONB 的原始拷贝(迁移前的解析逻辑)。"""
    row = (await session.execute(sa.text(
        "SELECT role_management, dept_gm_opm_mapping, dept_director_mapping,"
        " dept_supervisor_enabled FROM company_config LIMIT 1"))).first()
    rm = _as_dict(row[0]) if row else {}
    gm_opm = _as_dict(row[1]) if row else {}
    director = _as_dict(row[2]) if row else {}
    supervisor = _as_dict(row[3]) if row else {}
    return rm, gm_opm, director, supervisor


_SKIP = object()  # roles never sourced from company_config (e.g. dept_manager)


def _resolve(role: str, dept_id: str, rm: dict, gm_opm: dict, director: dict, supervisor: dict):
    """Resolve one workflow step's role to a comparable value for `dept_id`.

    Mirrors app/crud/engine.py's _build_role_map / _resolve_director /
    _resolve_supervisor, minus the per-user active-user check (both old and
    new getters feed the same users table, so that check can't diverge).
    """
    if role == "gm_or_opm":
        code = gm_opm.get(dept_id, "gm")
        return rm.get(f"{code}_user_id")
    if role in _POST_ROLES:
        return rm.get(f"{role}_user_id")
    if role == "finance_bp":
        return sorted(rm.get("finance_bp_user_ids") or [])
    if role == "director":
        return director.get(dept_id)
    if role == "supervisor":
        return bool(supervisor.get(dept_id, False))
    return _SKIP


async def main() -> int:
    async with AsyncSessionLocal() as session:
        old_rm, old_gm_opm, old_director, old_supervisor = await _old_readings(session)
        new_rm = await get_role_management(session)
        new_gm_opm = await get_dept_gm_opm_mapping(session)
        new_director = await get_dept_director_mapping(session)
        new_supervisor = await get_dept_supervisor_enabled(session)

        dept_rows = (await session.execute(sa.text(
            "SELECT id::text, code FROM departments WHERE is_active ORDER BY code"))).all()

        diffs: list[tuple[str, str, str, object, object]] = []
        for dept_id, code in dept_rows:
            for doc_type in _DOC_TYPES:
                steps = await get_workflow(session, doc_type)
                for step in steps:
                    role = step.get("role")
                    old_val = _resolve(role, dept_id, old_rm, old_gm_opm, old_director, old_supervisor)
                    if old_val is _SKIP:
                        continue
                    new_val = _resolve(role, dept_id, new_rm, new_gm_opm, new_director, new_supervisor)
                    if old_val != new_val:
                        diffs.append((code, doc_type, role, old_val, new_val))

        for code, doc_type, role, old_val, new_val in diffs:
            print(f"DIFF dept={code} doc={doc_type} step={role} old={old_val} new={new_val}")

        if diffs:
            print(f"PARITY FAILED: {len(diffs)} divergence(s) across "
                  f"{len(dept_rows)} depts x 3 doc types", file=sys.stderr)
            return 1

        print(f"PARITY OK ({len(dept_rows)} depts x 3 doc types)")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
