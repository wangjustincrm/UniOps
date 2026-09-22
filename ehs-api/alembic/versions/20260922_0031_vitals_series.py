"""Vitals become a series, because they are taken more than once.

The form recorded one set. A first aider takes them again — that is the whole
point of taking them: a pulse of 110 means one thing on its own and another
beside the 90 taken five minutes earlier, and the trend is what decides whether
somebody goes to hospital. The second acceptance round asked for it plainly.

The existing columns are backfilled into the first reading and then left alone.
Nothing outside the medical endpoint ever read them — checked, not assumed —
so there is no second consumer to keep in step, and dropping them on live
injury records is a risk with no upside.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0031" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260922_0031"
down_revision = "20260922_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ehs_incident_medical",
        sa.Column("vitals_series", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
    )
    # One reading per record that has any vitals at all, in the same shape the
    # form now writes. `pain_level` is a number and stays one; the rest are the
    # free text they were, because a blood pressure is written "120/80" and a
    # pulse is sometimes "110 weak".
    op.execute("""
        UPDATE ehs_incident_medical
           SET vitals_series = jsonb_build_array(jsonb_build_object(
                 'at',             coalesce(vitals_at_text, ''),
                 'pulse',          coalesce(pulse, ''),
                 'respirations',   coalesce(respirations, ''),
                 'blood_pressure', coalesce(blood_pressure, ''),
                 'pain_level',     pain_level,
                 'pain_location',  coalesce(pain_location, ''),
                 'other',          coalesce(vitals_other, '')))
         WHERE vitals_at_text IS NOT NULL
            OR pulse IS NOT NULL
            OR respirations IS NOT NULL
            OR blood_pressure IS NOT NULL
            OR pain_level IS NOT NULL
            OR pain_location IS NOT NULL
            OR vitals_other IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("ehs_incident_medical", "vitals_series")
