"""EPMS Direct PA: payment_applications.department_id; OA's rows re-tagged oa_direct

Revision ID: dp01_direct_pa
Revises: fr01_freight
Create Date: 2026-10-20

Direct PA moves from OA (retired) to EPMS. An EPMS Direct PA pays one or more
EPMS invoices that were matched to no PO and no agreement (invoices.match_route
= 'direct'); it carries its own department, cost centre and budget account on
the header, and walks the `pa_dir` workflow (approval-api engine.workflow_key).

* department_id — new. The department the payment is charged to; approval
  routing (dept_manager / gm_or_opm / director steps, when an admin adds them
  to the pa_dir chain) and restricted visibility read it.
* cost_center_id / budget_account_code — already physical (expense-api 0008
  and the OA baseline); EPMS now maps them too.
* source: fr01_freight backfilled every PA with neither po_id nor agreement_id
  as 'direct' — those are OA's PA-DIRs, whose invoice_ids hold expense_invoices
  ids. 'direct' now means the EPMS Direct PA, so OA's rows move to 'oa_direct'
  (also catches any OA row written after fr01 with the 'po' server default).
  Production had 0 such rows at fr01 time; dev databases may have some.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "dp01_direct_pa"
down_revision = "fr01_freight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No migration in any service ever created budget_account_code — it exists
    # on every database we have (finance-api reads it in production), but a
    # freshly built one would not have it. IF NOT EXISTS makes both a no-op
    # where they are already there.
    op.execute("ALTER TABLE payment_applications "
               "ADD COLUMN IF NOT EXISTS budget_account_code VARCHAR(100)")
    op.execute("ALTER TABLE payment_applications "
               "ADD COLUMN IF NOT EXISTS cost_center_id UUID")
    op.add_column("payment_applications", sa.Column(
        "department_id", UUID(as_uuid=True),
        sa.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_payment_applications_department_id", "payment_applications",
                    ["department_id"])
    # Database-level backstop for the row locks in api/v1/invoices.py: a
    # Direct-route invoice never carries a PO or agreement link.
    op.create_check_constraint(
        "ck_invoices_direct_no_po", "invoices",
        "match_route IS DISTINCT FROM 'direct' OR (po_id IS NULL AND agreement_id IS NULL)")
    op.execute("""
        UPDATE payment_applications
           SET source = 'oa_direct'
         WHERE po_id IS NULL AND agreement_id IS NULL
           AND source IN ('direct', 'po')
    """)


def downgrade() -> None:
    op.execute("UPDATE payment_applications SET source = 'direct' WHERE source = 'oa_direct'")
    op.drop_constraint("ck_invoices_direct_no_po", "invoices", type_="check")
    op.drop_index("ix_payment_applications_department_id", table_name="payment_applications")
    op.drop_column("payment_applications", "department_id")
