"""A signature that says who signed, in what role, and when.

HSE asked for this directly: "Name, role and timestamp should automatically be
entered. The worker's role should come from the employee table and the timestamp
should be logged automatically when the form is signed." Until now the record
held only the drawn image, so a signature on a printed report was a mark with no
attribution — the reader had to already know whose it was.

Three snapshot columns, following the rule every other name in this module
follows: the values are copied at signing time and never re-resolved. A person
who changes job next year has not changed what they signed as.

`signer_role` is a job title, not the UniOps `role` column — that holds
`requester` / `ehs_manager`, which describe system access, not what someone does
on the plant floor. Job titles live in `ehs_job_positions` / `ehs_worker_positions`,
which are empty today, so this will be NULL until HR populates them. That is
deliberate: an empty role prints nothing, whereas falling back to `role` would
print "requester" under someone's signature on a statutory record, which is both
wrong and hard to notice. HSE's own SiteDocs exports show "No title" against six
of seven signatories, so the gap is theirs as much as ours.

Revision ID: 20260831_0005
Revises: 20260831_0004
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op

revision = "20260831_0005"
down_revision = "20260831_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incidents", sa.Column("signer_name", sa.String(255), nullable=True))
    op.add_column("ehs_incidents", sa.Column("signer_role", sa.String(150), nullable=True))
    op.add_column("ehs_incidents",
                  sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))

    # Existing signed reports keep their image; the reporter is the only
    # attribution available for them, and the time they submitted is the closest
    # honest answer for when they signed.
    op.execute("""
        update ehs_incidents
           set signer_name = reported_by_name,
               signed_at   = coalesce(submitted_at, created_at)
         where signature is not null and signer_name is null
    """)


def downgrade() -> None:
    op.drop_column("ehs_incidents", "signed_at")
    op.drop_column("ehs_incidents", "signer_role")
    op.drop_column("ehs_incidents", "signer_name")
