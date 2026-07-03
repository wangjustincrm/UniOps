"""insert optional supervisor/director nodes into existing workflow_defs (merge-safe)

Revision ID: a2_workflow_defs_optional_levels
Revises: a1_supervisor_director

Idempotent, non-destructive: for every company_config row this inserts the
optional Supervisor step (PR only) immediately BEFORE the dept_manager step and
the optional Director step immediately AFTER the dept_manager step, WITHOUT
touching any other steps a deployment may have customised (e.g. a bespoke
ap_clerk step, a single-step PR chain, etc.). Steps already present are left
alone, so re-running is a no-op. Chains without a dept_manager step are skipped
(nowhere to anchor the insertion).
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "a2_workflow_defs_optional_levels"
down_revision = "a1_supervisor_director"
branch_labels = None
depends_on = None

SUPERVISOR = {"id": "supervisor", "role": "supervisor", "label": "Supervisor"}
DIRECTOR = {"id": "director", "role": "director", "label": "Director"}


def _merge(chain, add_supervisor):
    """Return (new_chain, changed). Insert director after dept_manager and,
    if add_supervisor, supervisor before dept_manager — only when missing."""
    if not isinstance(chain, list) or not chain:
        return chain, False
    roles = [s.get("role") for s in chain]
    if "dept_manager" not in roles:
        return chain, False  # no anchor — leave untouched
    result = list(chain)
    changed = False

    # Director: immediately after dept_manager
    if "director" not in roles:
        dm = next(i for i, s in enumerate(result) if s.get("role") == "dept_manager")
        result.insert(dm + 1, dict(DIRECTOR))
        changed = True

    # Supervisor: immediately before dept_manager (PR only)
    if add_supervisor and "supervisor" not in [s.get("role") for s in result]:
        dm = next(i for i, s in enumerate(result) if s.get("role") == "dept_manager")
        result.insert(dm, dict(SUPERVISOR))
        changed = True

    return result, changed


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, workflow_defs FROM company_config")).fetchall()
    for row_id, wf in rows:
        wf = dict(wf or {})
        changed = False

        pr, c_pr = _merge(wf.get("pr"), add_supervisor=True)
        if c_pr:
            wf["pr"] = pr
            changed = True

        pa, c_pa = _merge(wf.get("pa"), add_supervisor=False)
        if c_pa:
            wf["pa"] = pa
            changed = True

        if changed:
            conn.execute(
                sa.text("UPDATE company_config SET workflow_defs = CAST(:wf AS jsonb) WHERE id = :id"),
                {"wf": json.dumps(wf), "id": str(row_id)},
            )


def downgrade() -> None:
    pass  # non-destructive
