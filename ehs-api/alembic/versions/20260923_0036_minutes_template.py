"""The minutes form becomes a form, configurable in Settings.

Minutes were one free-text box and an uploaded PDF. HSE asked for the format
to be configurable "in the settings/forms area to allow for configuration and
readability changes that happen over time" — which is what the form builder
already is, so the minutes use it rather than growing a second one.

The pointer lives on the meeting rather than a generic source link on the
submission: a meeting owns its minutes, there is exactly one set, and a
polymorphic link would be a mechanism built for a single caller.

Both columns are nullable and nothing is backfilled. A meeting written before
this keeps its free-text minutes and still reads; configuring a template
changes what the next meeting is offered, not what the last one recorded.
That is also what makes this safe to turn on midway through a year.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260923_0036" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260923_0036"
down_revision = "20260923_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_jhsc_meetings",
                  sa.Column("minutes_submission_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_ehs_jhsc_meetings_minutes_submission",
        "ehs_jhsc_meetings", "ehs_form_submissions",
        ["minutes_submission_id"], ["id"], ondelete="SET NULL",
    )
    # Which template the minutes use. A code rather than an id so that
    # exporting settings between environments does not carry a uuid that means
    # nothing on the other side.
    op.add_column("ehs_config",
                  sa.Column("jhsc_minutes_template_code", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_config", "jhsc_minutes_template_code")
    op.drop_constraint("fk_ehs_jhsc_meetings_minutes_submission",
                       "ehs_jhsc_meetings", type_="foreignkey")
    op.drop_column("ehs_jhsc_meetings", "minutes_submission_id")
