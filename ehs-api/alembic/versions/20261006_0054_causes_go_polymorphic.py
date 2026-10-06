"""Causes belong to whatever found them, not only to an incident

Round 5, answer box 11: "The hazard register needs further development including
adding a risk assessment like on the incident form, the what was found, immediate
causes and root cause, and corrective actions all need to be added ot the hazard
register form."

Immediate and root causes existed on an incident investigation and nowhere else.
A second near-identical table for hazards — and a third when job analyses want
the same — is the thing to avoid, so the one table takes causes from more than
one kind of record.

★ Two nullable foreign keys and a CHECK, **not** the polymorphic
`(source_type, source_id)` pair this module uses for `ehs_actions`. That was my
first attempt and uniops-41 argued it down, correctly:

  · `incident_id` carries a working `ON DELETE CASCADE`. Going polymorphic would
    give that up and buy nothing except one fewer column when JSA arrives.
  · causes come from at most three kinds of record — an incident, a hazard, and
    one day a job analysis. `ehs_actions` has seven or eight, which is what makes
    the polymorphic pair worth its cost *there*.
  · the strongest argument is my own migration 0051: its downgrade needs a guard
    precisely because deleting a record would otherwise leave orphan causes
    behind. Choosing a shape that produces orphans, and then writing guards for
    them, is backwards.

★ The table is still renamed. The FK question and the name are separate, and a
table called `ehs_incident_causes` holding a hazard's root causes misleads the
next reader more than a plain name costs.

★ Migration 0051 is deliberately **not** updated to the new name. Migrations
replay in order: at 0051's point the table is still `ehs_incident_causes`, and a
full downgrade runs this file's downgrade — which renames it back — before 0051's
is reached. Pointing 0051 at `ehs_causes` would have made the chain fail on a
table that does not exist yet at that revision.

Revision ID: 20261006_0054
Revises: 20261005_0053
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261006_0054"
down_revision = "20261005_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("ehs_incident_causes", "ehs_causes")
    # Renamed with it. A primary key still called `ehs_incident_causes_pkey` on a
    # table called `ehs_causes` is the same misleading name one level down.
    op.execute("ALTER INDEX ehs_incident_causes_pkey RENAME TO ehs_causes_pkey")
    op.execute("ALTER INDEX ix_ehs_incident_causes_incident_id "
               "RENAME TO ix_ehs_causes_incident_id")
    op.execute("ALTER TABLE ehs_causes RENAME CONSTRAINT "
               "ehs_incident_causes_incident_id_fkey TO ehs_causes_incident_id_fkey")

    # The incident becomes optional, because a hazard's causes have none.
    op.alter_column("ehs_causes", "incident_id", nullable=True)
    op.add_column("ehs_causes", sa.Column(
        "hazard_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("ehs_hazards.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_ehs_causes_hazard_id", "ehs_causes", ["hazard_id"])

    # ★ Exactly one owner. Without this a row can belong to both or to neither,
    # and a cause belonging to nothing is invisible to every screen while still
    # being counted by anything that totals the table.
    op.create_check_constraint(
        "ck_ehs_causes_one_owner", "ehs_causes",
        "num_nonnulls(incident_id, hazard_id) = 1")


def downgrade() -> None:
    bind = op.get_bind()
    stranded = bind.execute(sa.text(
        "SELECT COUNT(*) FROM ehs_causes WHERE hazard_id IS NOT NULL")).scalar() or 0
    if stranded:
        # ★ Refused with the count rather than dropped. A root cause recorded
        # against a hazard is a record, and losing it quietly to a rollback is
        # worse than a rollback that stops and says why.
        raise RuntimeError(
            f"{stranded} cause(s) belong to a hazard, and the incident-only table "
            "has nowhere to put them. Move or remove them first, then re-run the "
            "downgrade.")

    op.drop_constraint("ck_ehs_causes_one_owner", "ehs_causes", type_="check")
    op.drop_index("ix_ehs_causes_hazard_id", table_name="ehs_causes")
    op.drop_column("ehs_causes", "hazard_id")
    op.alter_column("ehs_causes", "incident_id", nullable=False)
    op.execute("ALTER TABLE ehs_causes RENAME CONSTRAINT "
               "ehs_causes_incident_id_fkey TO ehs_incident_causes_incident_id_fkey")
    op.execute("ALTER INDEX ix_ehs_causes_incident_id "
               "RENAME TO ix_ehs_incident_causes_incident_id")
    op.execute("ALTER INDEX ehs_causes_pkey RENAME TO ehs_incident_causes_pkey")
    op.rename_table("ehs_causes", "ehs_incident_causes")
