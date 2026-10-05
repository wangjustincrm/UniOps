"""A training record points at the session it came from

Round 5, TC-08: "report has a heading called HOW and the data shows here. remove
how and add the run by name and the instructors name."

Who ran a session and who taught it are on the session. A training record had no
link to one — the gap round 4 noted and left open — so the report could not name
either of them, and `HOW` (internal or external) was standing where the useful
columns should be.

★ The link, not a copy of the names. Two of the three facts HSE wants printed can
be corrected after the fact — the instructor is filled in by hand and a typo gets
fixed — and a snapshot on the record would print the old spelling forever while
the session showed the new one. One source, read through the join.

★ The backfill matches on the enrolment: same person, same course, and the
session's date equal to the record's completion date. Where that matches more
than one session, or none, the record is left unlinked rather than guessed at. An
unlinked record prints a dash, which is true; a wrongly linked one would print
somebody else's name against a training record, which is evidence of the wrong
thing.

Revision ID: 20261005_0052
Revises: 20261005_0051
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261005_0052"
down_revision = "20261005_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ehs_training_records",
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_ehs_training_records_delivery", "ehs_training_records",
        "ehs_course_deliveries", ["delivery_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_ehs_training_records_delivery", "ehs_training_records",
                    ["delivery_id"])

    # One session, unambiguously, or nothing.
    op.execute("""
        UPDATE ehs_training_records r
           SET delivery_id = m.delivery_id
          FROM (
              SELECT e.user_id, d.course_id, d.session_date,
                     (array_agg(d.id))[1] AS delivery_id, COUNT(*) AS matches
                FROM ehs_course_enrolments e
                JOIN ehs_course_deliveries d ON d.id = e.delivery_id
               GROUP BY e.user_id, d.course_id, d.session_date
              HAVING COUNT(*) = 1
          ) m
         WHERE r.delivery_id IS NULL
           AND r.user_id = m.user_id
           AND r.course_id = m.course_id
           AND r.completed_on = m.session_date
    """)


def downgrade() -> None:
    op.drop_index("ix_ehs_training_records_delivery", table_name="ehs_training_records")
    op.drop_constraint("fk_ehs_training_records_delivery", "ehs_training_records",
                       type_="foreignkey")
    op.drop_column("ehs_training_records", "delivery_id")
