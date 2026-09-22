"""The supervisor on Section B becomes a person, not a typed name.

`supervisor_name` was free text. HSE asked for a dropdown, and the reason is
not tidiness: the supervisor named on an injury report is who the follow-up
goes to, and a typed name cannot be sent anything. Two spellings of the same
person also read as two people in any report grouped by supervisor.

The name column stays and stays populated. It is the snapshot — the record has
to keep saying who was named at the time even after that person leaves and the
user row is gone. Same rule as every other `*_name` in this module.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0028" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0028"
down_revision = "20260916_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ehs_incident_medical",
        sa.Column("supervisor_id", sa.UUID(), nullable=True),
    )
    # SET NULL rather than RESTRICT: a supervisor leaving the company must not
    # make an injury record undeletable or, worse, block the user row from
    # ever being cleaned up. The name snapshot is what keeps the record
    # readable afterwards.
    op.create_foreign_key(
        "fk_ehs_incident_medical_supervisor",
        "ehs_incident_medical", "users",
        ["supervisor_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ehs_incident_medical_supervisor",
                       "ehs_incident_medical", type_="foreignkey")
    op.drop_column("ehs_incident_medical", "supervisor_id")
