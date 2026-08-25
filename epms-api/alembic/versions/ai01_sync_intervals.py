"""company_config: JV / MDM 同步间隔列

采购同步的间隔列(nc_purchase_sync_interval_minutes)早就在这张表上了。
JV 与 MDM 的调度器沿用同一套语义,所以各加一列,而不是另起一张表:
  NULL = 没人设过 -> 各自的默认值
  0    = 关闭自动同步
  >0   = 分钟数(上限由各自的 MAX_INTERVAL_MINUTES 钳制)

Revision ID: ai01_sync_intervals
Revises: ai01_pr_service_completion
"""
import sqlalchemy as sa
from alembic import op

revision = "ai01_sync_intervals"
down_revision = "ai01_pr_service_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_config",
                  sa.Column("nc_jv_sync_interval_minutes", sa.Integer(), nullable=True))
    op.add_column("company_config",
                  sa.Column("erp_mdm_sync_interval_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("company_config", "erp_mdm_sync_interval_minutes")
    op.drop_column("company_config", "nc_jv_sync_interval_minutes")
