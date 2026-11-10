"""Only supervisors and HSE verify a corrective action.

Round 12, Q7: "verification of CAPA's can only be completed by supervisors or
HSE." Reverses half of 0018: JHSC members no longer hold ehs.action.verify.
(The other half of 0018, an owner verifying their own, was a rule in ehs-api
and is removed there.) ehs_manager, ehs_coordinator and area_supervisor keep it.

Revision ID: 0027_capa_verify_sup_hse
Revises: 0026_merge_main_and_ehs
"""
from alembic import op

revision = "0027_capa_verify_sup_hse"
down_revision = "0026_merge_main_and_ehs"
branch_labels = None
depends_on = None

_KEY = "ehs.action.verify"


def upgrade() -> None:
    op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}' "
               "AND role_code = 'jhsc_member'")


def downgrade() -> None:
    op.execute("INSERT INTO role_permissions(role_code,permission_key) "
               f"VALUES ('jhsc_member','{_KEY}') ON CONFLICT DO NOTHING")
