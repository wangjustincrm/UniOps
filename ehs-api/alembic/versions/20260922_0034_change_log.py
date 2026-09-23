"""Every field change on every form, recorded.

"There needs to be an audit trail on every form." Taken literally, which is
how it was asked: not the key actions — submit, approve, sign, close, which
were already kept — but each field, with what it was and what it became.

Recorded at the ORM flush rather than in each write path. Thirty-odd endpoints
write these tables and a line added to each is a line that will be forgotten in
the next one; a trail with a hole in it is worse than no trail, because it
reads as a complete account.

Values are held as text whatever the column's type. This is a record of what
somebody saw and typed, not a second copy of the data to compute on, and a
typed column would need widening every time a new field is logged.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0034" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0034"
down_revision = "20260922_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_change_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("table_name", sa.String(63), nullable=False),
        # Text, not uuid: most of this module keys on a uuid and the config
        # table does not, and a change to a configuration row is exactly the
        # kind of thing an audit asks about. A column that fits only the common
        # case would have excluded it silently.
        sa.Column("row_id", sa.String(64), nullable=False),
        # The document number where the row has one, so a trail can be read
        # without joining back to a record that may since have been deleted.
        sa.Column("row_ref", sa.String(40), nullable=True),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("field", sa.String(63), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.UUID(), nullable=True),
        sa.Column("changed_by_name", sa.String(255), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
    )
    # The only query this table serves: one record's trail, newest first.
    op.create_index("ix_ehs_change_log_row", "ehs_change_log",
                    ["table_name", "row_id", "changed_at"])

    # Append-only, enforced by the database rather than by everybody
    # remembering. A trail somebody can quietly edit is not evidence, and this
    # module already applies the same rule to incidents and training records.
    op.execute("""
        CREATE OR REPLACE FUNCTION ehs_change_log_is_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ehs_change_log is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_ehs_change_log_append_only
        BEFORE UPDATE OR DELETE ON ehs_change_log
        FOR EACH ROW EXECUTE FUNCTION ehs_change_log_is_append_only()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ehs_change_log_append_only ON ehs_change_log")
    op.execute("DROP FUNCTION IF EXISTS ehs_change_log_is_append_only()")
    op.drop_index("ix_ehs_change_log_row", table_name="ehs_change_log")
    op.drop_table("ehs_change_log")
