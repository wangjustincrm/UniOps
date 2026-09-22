"""A drill gets a root cause, a signature, and photographs.

The evaluation captured what worked and what did not, in prose, and stopped
there. HSE asked for the three things that make it evidence rather than a note:
why it went the way it did, drawn from the same root cause list the incident
investigations use so the two can be counted together; who says this is what
happened, signed; and what the muster point actually looked like.

The root cause is stored as id and label together — the id is what a report
groups on, the label is the snapshot that keeps a three-year-old drill readable
after somebody renames the vocabulary entry. Same rule as everywhere else here.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0029" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0029"
down_revision = "20260922_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_drills", sa.Column("root_cause_id", sa.UUID(), nullable=True))
    op.add_column("ehs_drills", sa.Column("root_cause_label", sa.String(200), nullable=True))
    # Base64 PNG drawn on the pad, the same shape every other signature in this
    # module uses. Kept on the row rather than in file-api: it is part of the
    # record, not an attachment to it, and a drill report that renders without
    # its signature is not the document that was signed.
    op.add_column("ehs_drills", sa.Column("signature", sa.Text(), nullable=True))
    op.add_column("ehs_drills", sa.Column("signed_by", sa.UUID(), nullable=True))
    op.add_column("ehs_drills", sa.Column("signed_by_name", sa.String(255), nullable=True))
    op.add_column("ehs_drills",
                  sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_ehs_drills_signed_by", "ehs_drills", "users",
        ["signed_by"], ["id"], ondelete="SET NULL",
    )

    # The bytes live in file-api; this row is the join, because file-api has no
    # "list by document" endpoint and the owning service is what knows which
    # files belong to which record.
    op.create_table(
        "ehs_drill_attachments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("drill_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["drill_id"], ["ehs_drills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_ehs_drill_attachments_drill_id", "ehs_drill_attachments", ["drill_id"])


def downgrade() -> None:
    op.drop_index("ix_ehs_drill_attachments_drill_id", table_name="ehs_drill_attachments")
    op.drop_table("ehs_drill_attachments")
    op.drop_constraint("fk_ehs_drills_signed_by", "ehs_drills", type_="foreignkey")
    for column in ("signed_at", "signed_by_name", "signed_by", "signature",
                   "root_cause_label", "root_cause_id"):
        op.drop_column("ehs_drills", column)
