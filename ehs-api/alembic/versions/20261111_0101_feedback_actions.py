"""Corrective actions raised on a feedback comment (round 12, ACT-04).

Revision ID: 20261111_0101
Revises: 20261111_0100
"""
from alembic import op

revision = "20261111_0101"
down_revision = "20261111_0100"
branch_labels = None
depends_on = None

_BEFORE = ("incident", "inspection", "jhsc", "audit", "observation",
           "hazard", "drill", "permit", "manual", "asset")
_AFTER = _BEFORE + ("feedback",)


def _check(types) -> str:
    return "source_type IN (" + ", ".join(f"'{s}'" for s in types) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_ehs_action_source_type", "ehs_actions", type_="check")
    op.create_check_constraint("ck_ehs_action_source_type", "ehs_actions", _check(_AFTER))


def downgrade() -> None:
    op.drop_constraint("ck_ehs_action_source_type", "ehs_actions", type_="check")
    op.create_check_constraint("ck_ehs_action_source_type", "ehs_actions", _check(_BEFORE))
