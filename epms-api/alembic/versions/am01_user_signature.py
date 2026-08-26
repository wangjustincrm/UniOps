"""users.signature_image — 用户在 My Profile 里预设的签名

PO 签字流程(NC 导入单的两级线下签字搬进系统)需要在 PDF 上盖签名图。
签名由用户自己在 Profile 里手写或上传，存成 base64 `data:image/...` URL。

列归属:users 表的 schema 一直由 epms-api 的 alembic 维护(见
identity-api/app/models/user.py 顶部注释),identity-api 只维护镜像模型,
所以这一列加在这里而不是 identity 的迁移链上。

用 Text 而不是定长:base64 后的手写签名约 5-30 KB,上传图更大;
入口处由 identity 的 UpdateMeRequest.MAX_SIGNATURE_CHARS 兜住上限。

Revision ID: am01_user_signature
Revises: ai01_sync_intervals
"""
import sqlalchemy as sa
from alembic import op

revision = "am01_user_signature"
down_revision = "ai01_sync_intervals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("signature_image", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "signature_image")
