"""A document can be sent to named people as well as to roles.

The audience was roles only, and the third round asked for specific
individuals: a procedure that concerns four people on one line does not
correspond to any role, and sending it to everybody who holds the nearest one
trains thirty others to ignore the next thing they are sent.

Additive. A document with roles and nobody named behaves exactly as before, and
one with both goes to the union — naming somebody adds them, it does not
narrow the roles.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260925_0044" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260925_0044"
down_revision = "20260925_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_documents", sa.Column(
        "ack_user_ids", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("ehs_documents", "ack_user_ids")
