"""A hazard records who recorded it

Round 5, answer box 11: "supervisors require a tbt, hazard assessment and a near
miss to be completed each month … a hazard assessment is a hazard register."

To say whether a supervisor recorded a hazard this month, a hazard has to know
who recorded it. It did not. `create_hazard` was handed the creator and used it
only as the default *owner* — so whenever somebody recorded a hazard and named
someone else to own it, who recorded it was lost. (`created_by` in this module's
hazard file belongs to the job analysis, not the hazard; easy to misread.)

★ Backfilled from the audit trail, and only from there. Every insert into
`ehs_hazards` since the change log was switched on carries its actor, so those
are exact. The hazards recorded before that are left empty rather than filled
from `owner_id`: the owner can be somebody else, and a monthly count that credits
a supervisor with a hazard they were merely handed is the wrong answer to exactly
the question this column exists for.

Revision ID: 20261007_0056
Revises: 20261006_0055
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0056"
down_revision = "20261006_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_hazards", sa.Column(
        "created_by", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("ehs_hazards", sa.Column(
        "created_by_name", sa.String(length=255), nullable=True))
    op.create_index("ix_ehs_hazards_created_by", "ehs_hazards", ["created_by"])

    # The earliest insert per hazard, in case a row was ever inserted twice.
    #
    # ★ The name falls back to the users table when the log carried only the id.
    # On the dev database two of the three recoverable inserts recorded who but
    # not their name; the id is exact, so looking the name up is not a guess —
    # and a creator with no name beside them is the half-snapshot every other
    # `*_name` column in this module exists to avoid.
    op.execute("""
        UPDATE ehs_hazards h
           SET created_by = c.changed_by,
               created_by_name = COALESCE(c.changed_by_name, u.full_name)
          FROM (
              SELECT DISTINCT ON (row_id) row_id, changed_by, changed_by_name
                FROM ehs_change_log
               WHERE table_name = 'ehs_hazards'
                 AND action = 'insert'
                 AND changed_by IS NOT NULL
               ORDER BY row_id, changed_at
          ) c
          LEFT JOIN users u ON u.id = c.changed_by
         WHERE h.id::text = c.row_id
           AND h.created_by IS NULL
    """)

    # The three numbers, so the release log says what happened rather than
    # "it ran". Hazards recorded before the change log was switched on stay empty.
    bind = op.get_bind()
    filled, empty, total = bind.execute(sa.text("""
        SELECT COUNT(*) FILTER (WHERE created_by IS NOT NULL),
               COUNT(*) FILTER (WHERE created_by IS NULL),
               COUNT(*)
          FROM ehs_hazards
    """)).one()
    print(f"0056: recorder known for {filled} of {total} hazards; "
          f"{empty} left empty (recorded before the change log existed)")


def downgrade() -> None:
    op.drop_index("ix_ehs_hazards_created_by", table_name="ehs_hazards")
    op.drop_column("ehs_hazards", "created_by_name")
    op.drop_column("ehs_hazards", "created_by")
