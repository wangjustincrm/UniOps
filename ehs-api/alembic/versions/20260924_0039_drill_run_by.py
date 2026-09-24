"""Who ran the drill, by name.

A drill record is signed, and a signature is a picture of a name. Asked to say
who ran the evacuation, the report offered only that picture — which HSE
reported as unreadable, and which is the wrong field anyway: the person who
signs the record off is not always the person who ran the thing.

Both halves, as everywhere else in this module: the id is the link to the
person, the name is the snapshot that survives them leaving. The foreign key
sets null on delete rather than taking the drill with it.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260924_0039" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260924_0039"
down_revision = "20260924_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_drills", sa.Column("run_by", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_drills", sa.Column("run_by_name", sa.String(255), nullable=True))
    op.create_foreign_key(
        "fk_ehs_drills_run_by_users", "ehs_drills", "users",
        ["run_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_ehs_drills_run_by_users", "ehs_drills", type_="foreignkey")
    op.drop_column("ehs_drills", "run_by_name")
    op.drop_column("ehs_drills", "run_by")
