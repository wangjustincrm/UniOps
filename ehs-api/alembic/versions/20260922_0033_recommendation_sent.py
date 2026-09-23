"""A JHSC recommendation records who it went to and when.

OHSA s.9(20) gives the employer twenty-one days to reply in writing, and the
recommendation itself carried only the reply half — the response, who wrote it,
when. Who it was put in front of, and on what date, was nowhere: the committee
could see that nobody had answered and not who had failed to.

★ Sending does NOT move the clock. It starts when the recommendation is
recorded, which is the conservative reading already documented in
deadlines.py — if sending set the start, not sending would extend a statutory
deadline indefinitely, and the one thing this module must never do is let an
omission look like compliance.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0033" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260922_0033"
down_revision = "20260922_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_jhsc_recommendations",
                  sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_jhsc_recommendations",
                  sa.Column("sent_by", sa.UUID(), nullable=True))
    op.add_column("ehs_jhsc_recommendations",
                  sa.Column("sent_by_name", sa.String(255), nullable=True))
    # Names as well as ids: the record has to stay readable after somebody
    # leaves and the user row is gone, same rule as every other person
    # reference in this module.
    op.add_column("ehs_jhsc_recommendations",
                  sa.Column("sent_to", postgresql.JSONB(), nullable=False,
                            server_default=sa.text("'[]'::jsonb")))
    op.create_foreign_key(
        "fk_ehs_jhsc_rec_sent_by", "ehs_jhsc_recommendations", "users",
        ["sent_by"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ehs_jhsc_rec_sent_by", "ehs_jhsc_recommendations",
                       type_="foreignkey")
    for column in ("sent_to", "sent_by_name", "sent_by", "sent_at"):
        op.drop_column("ehs_jhsc_recommendations", column)
