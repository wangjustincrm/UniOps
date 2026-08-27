"""PO 签字流程:purchase_orders 的 signoff_* 列 + po_signoff_signatures 表

NC 导入的采购订单此前靠打印 PDF 线下找 Purchasing Manager 草签、OPM 全签,
本次把这个过程搬进系统。签字状态**必须**与 purchase_orders.status 正交:
nc_purchase_sync/writer.py 每次同步都无条件重写 status(来自 NC 的
forderstatus),挂在那上面的任何签字状态下一轮就没了。

signoff_status 的值域直接沿用审批引擎写入的那套字面量
(draft/submitted/in_review/approved/returned/rejected/cancelled),
引擎经 _DOC_META 的 status_attr / step_attr 间接层读写这两列。
server_default 让存量 NC 单(约 1700 张,都已在 NC 审批通过)一律落到
'draft' = 从未发起签字,不会被卷进流程。

po_signoff_signatures 存的是签字瞬间的**快照**(图 + 姓名 + 时间),
不是指向 users.signature_image 的引用 —— PO 的 PDF 可以随时重新生成,
读实时签名会让改过签名或已离职的人把历史单上的签名换掉。

Revision ID: am02_po_signoff
Revises: am01_user_signature
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "am02_po_signoff"
down_revision = "am01_user_signature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column(
        "signoff_status", sa.String(length=20), nullable=False, server_default="draft"))
    op.add_column("purchase_orders", sa.Column(
        "signoff_step_idx", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("purchase_orders", sa.Column(
        "signoff_submitted_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("purchase_orders", sa.Column(
        "signoff_submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_purchase_orders_signoff_submitted_by_users",
        "purchase_orders", "users", ["signoff_submitted_by"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_purchase_orders_signoff_status", "purchase_orders", ["signoff_status"])

    op.create_table(
        "po_signoff_signatures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_idx", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("sig_slot", sa.String(length=20), nullable=True),
        sa.Column("signed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signer_name", sa.String(length=255), nullable=False),
        sa.Column("signature_image", sa.Text(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["po_id"], ["purchase_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("po_id", "step_idx", name="uq_po_signoff_signatures_po_step"),
    )
    op.create_index("ix_po_signoff_signatures_po_id", "po_signoff_signatures", ["po_id"])


def downgrade() -> None:
    op.drop_index("ix_po_signoff_signatures_po_id", table_name="po_signoff_signatures")
    op.drop_table("po_signoff_signatures")
    op.drop_index("ix_purchase_orders_signoff_status", table_name="purchase_orders")
    op.drop_constraint("fk_purchase_orders_signoff_submitted_by_users",
                       "purchase_orders", type_="foreignkey")
    op.drop_column("purchase_orders", "signoff_submitted_at")
    op.drop_column("purchase_orders", "signoff_submitted_by")
    op.drop_column("purchase_orders", "signoff_step_idx")
    op.drop_column("purchase_orders", "signoff_status")
