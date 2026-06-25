"""aux_dimension_types catalog + posting_line_dimensions side table +
   COA NC-parity metadata + posting_lines.department_id (Phase a A1.7)

NC parity for the chart of accounts:
- 辅助核算 becomes an extensible CATALOG (aux_dimension_types), not a fixed
  enum. Seeds the dimensions the legacy chart uses (部门/收支项目/销售类型/
  国家地区/银行/物料/客商/项目/批次/仓库/渠道/成本中心).
- Hybrid spine storage: high-frequency dims are posting_lines columns
  (partner/cost_center/item/lot/warehouse/project/channel + new department);
  long-tail dims live in posting_line_dimensions (line_id, dim_code, value).
- COA gains 数量核算/默认计量单位/默认币种/生效日期/现金分类/助记码/表外.

Revision ID: 0010_aux_catalog
Revises: 0009_bank_recon
Create Date: 2026-06-15
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_aux_catalog"
down_revision = "0009_bank_recon"
branch_labels = None
depends_on = None


def upgrade():
    aux_types = op.create_table(
        "aux_dimension_types",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        # master_source: which master data backs this dimension (free | partner |
        # cost_center | department | item | project | bank_account | ...)
        sa.Column("master_source", sa.String(40), nullable=True),
        # storage: 'column' (a posting_lines column) | 'aux_table' (side table)
        sa.Column("storage", sa.String(12), nullable=False, server_default="aux_table"),
        sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="100"),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "posting_line_dimensions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("posting_line_id", UUID(as_uuid=True),
                  sa.ForeignKey("posting_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dim_code", sa.String(40), nullable=False),
        sa.Column("value_id", UUID(as_uuid=True), nullable=True),
        sa.Column("value_text", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_posting_line_dimensions_line", "posting_line_dimensions", ["posting_line_id"])
    op.create_index("ix_posting_line_dimensions_dim", "posting_line_dimensions", ["dim_code", "value_id"])

    # high-frequency dim added to the spine
    op.add_column("posting_lines", sa.Column("department_id", UUID(as_uuid=True), nullable=True))

    # COA NC-parity metadata (all nullable / defaulted — control features
    # like 受控模块/凭证必输项/方向控制 stay for the GL stage)
    op.add_column("chart_of_accounts",
                  sa.Column("quantity_accounting", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("chart_of_accounts", sa.Column("default_uom", sa.String(20), nullable=True))
    op.add_column("chart_of_accounts", sa.Column("default_currency", sa.String(10), nullable=True))
    op.add_column("chart_of_accounts", sa.Column("effective_from", sa.Date, nullable=True))
    op.add_column("chart_of_accounts", sa.Column("effective_to", sa.Date, nullable=True))
    op.add_column("chart_of_accounts", sa.Column("cash_flow_category", sa.String(50), nullable=True))
    op.add_column("chart_of_accounts", sa.Column("mnemonic", sa.String(40), nullable=True))
    op.add_column("chart_of_accounts",
                  sa.Column("is_off_balance", sa.Boolean, nullable=False, server_default=sa.false()))

    def t(code, name, master, storage, order, builtin=True):
        return dict(id=uuid.uuid4(), code=code, name=name, master_source=master,
                    storage=storage, builtin=builtin, is_active=True,
                    sort_order=order, entity_id=None)

    # storage='column' must match real posting_lines columns
    op.bulk_insert(aux_types, [
        t("partner",            "Partner (Vendor/Customer)", "partner",      "column", 10),
        t("cost_center",        "Cost Center",               "cost_center",  "column", 20),
        t("department",         "Department",                "department",   "column", 30),
        t("item",               "Item / Material",           "item",         "column", 40),
        t("project",            "Project",                   "project",      "column", 50),
        t("lot",                "Lot / Batch",               "lot",          "column", 60),
        t("warehouse",          "Warehouse",                 "warehouse",    "column", 70),
        t("channel",            "Channel",                   "free",         "column", 80),
        t("income_expense_item","Income / Expense Item",     "free",         "aux_table", 90),
        t("sales_type",         "Sales Type",                "free",         "aux_table", 100),
        t("country_region",     "Country / Region",          "free",         "aux_table", 110),
        t("bank_account",       "Bank Account",              "bank_account", "aux_table", 120),
        t("bank_category",      "Bank Category",             "free",         "aux_table", 130),
    ])


def downgrade():
    for col in ("is_off_balance", "mnemonic", "cash_flow_category", "effective_to",
                "effective_from", "default_currency", "default_uom", "quantity_accounting"):
        op.drop_column("chart_of_accounts", col)
    op.drop_column("posting_lines", "department_id")
    op.drop_table("posting_line_dimensions")
    op.drop_table("aux_dimension_types")
