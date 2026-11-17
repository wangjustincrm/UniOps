"""fr04: project-related freight shipments

Revision ID: fr04_freight_project_shipments
Revises: tz01_company_timezone
Create Date: 2026-11-17

A shipment may be marked "Project related": the registrar types the project
details instead of choosing a department and cost center, so both columns
become nullable. When the carrier's invoice is matched, such a line is
pre-filled with the company's project-freight cost center
(company_config.freight_project_cost_center_id) and must be confirmed by the
person matching the invoice before it can be finalised.

The setting is seeded with the R&D department's cost center when exactly one
active R&D department has exactly one active cost center; otherwise it stays
empty for the Freight admin to set under Freight -> Carriers.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fr04_freight_project_shipments"
down_revision = "tz01_company_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("freight_shipments", sa.Column(
        "is_project", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("freight_shipments", sa.Column("project_info", sa.Text(), nullable=True))
    op.alter_column("freight_shipments", "cost_center_id", nullable=True)
    op.alter_column("freight_shipments", "department_id", nullable=True)
    op.create_check_constraint(
        "ck_freight_shipment_cc_or_project", "freight_shipments",
        "(is_project AND project_info IS NOT NULL AND cost_center_id IS NULL"
        " AND department_id IS NULL)"
        " OR (NOT is_project AND cost_center_id IS NOT NULL AND department_id IS NOT NULL)")

    op.add_column("company_config", sa.Column(
        "freight_project_cost_center_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("cost_centers.id", ondelete="SET NULL"), nullable=True))
    op.execute("""
        UPDATE company_config SET freight_project_cost_center_id = (
            SELECT c.id FROM cost_centers c JOIN departments d ON d.id = c.department_id
            WHERE d.is_active AND c.is_active AND upper(trim(d.name)) = 'R&D')
        WHERE (SELECT count(*) FROM cost_centers c JOIN departments d ON d.id = c.department_id
               WHERE d.is_active AND c.is_active AND upper(trim(d.name)) = 'R&D') = 1
    """)


def downgrade() -> None:
    op.drop_column("company_config", "freight_project_cost_center_id")
    op.drop_constraint("ck_freight_shipment_cc_or_project", "freight_shipments", type_="check")
    # Project shipments have no cost center; refuse rather than drop data.
    n = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM freight_shipments WHERE is_project")).scalar()
    if n:
        raise RuntimeError(f"{n} project-related shipment(s) exist — give them a cost center "
                           "(or void and delete them) before downgrading fr04")
    op.alter_column("freight_shipments", "department_id", nullable=False)
    op.alter_column("freight_shipments", "cost_center_id", nullable=False)
    op.drop_column("freight_shipments", "project_info")
    op.drop_column("freight_shipments", "is_project")
