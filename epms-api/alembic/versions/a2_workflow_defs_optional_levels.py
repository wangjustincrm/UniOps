"""insert supervisor/director nodes into existing workflow_defs

Revision ID: a2_workflow_defs_optional_levels
Revises: a1_supervisor_director
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "a2_workflow_defs_optional_levels"
down_revision = "a1_supervisor_director"
branch_labels = None
depends_on = None

PR = [
    {"id": "supervisor",   "role": "supervisor",   "label": "Supervisor"},
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    {"id": "director",     "role": "director",     "label": "Director"},
    {"id": "gm_or_opm",    "role": "gm_or_opm",    "label": "GM / OPM"},
]
PA = [
    {"id": "dept_manager", "role": "dept_manager",   "label": "Department Manager"},
    {"id": "director",     "role": "director",       "label": "Director"},
    {"id": "gm_or_opm",    "role": "gm_or_opm",      "label": "GM / OPM"},
    {"id": "finance_bp",   "role": "finance_bp",     "label": "Finance BP"},
    {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
]

def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE company_config
        SET workflow_defs = jsonb_set(
            jsonb_set(COALESCE(workflow_defs, '{}'::jsonb), '{pr}', CAST(:pr AS jsonb), true),
            '{pa}', CAST(:pa AS jsonb), true)
    """), {"pr": json.dumps(PR), "pa": json.dumps(PA)})

def downgrade() -> None:
    pass  # non-destructive
