"""What an in-house certificate says.

Training completed here produced no certificate at all — only a course taken
outside had one, because that arrives as a file somebody uploads. The third
round asked for one the plant issues itself, visible to the person who earned
it and to whoever taught it.

The wording is configurable rather than fixed because a certificate is a piece
of the company's own paper: the heading, the line under the name and who signs
it are the parts a plant wants to say in its own words. Everything that matters
for the record — who, which course, when, until when, and the record number —
is not configurable, because those are what make it evidence rather than
decoration.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260925_0045" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260925_0045"
down_revision = "20260925_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column("certificate_heading", sa.String(120), nullable=True))
    op.add_column("ehs_config", sa.Column("certificate_body", sa.Text, nullable=True))
    op.add_column("ehs_config", sa.Column("certificate_signatory", sa.String(150), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_config", "certificate_signatory")
    op.drop_column("ehs_config", "certificate_body")
    op.drop_column("ehs_config", "certificate_heading")
