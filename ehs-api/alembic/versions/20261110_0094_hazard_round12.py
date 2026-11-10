"""Hazards, round 12: several kinds and controls, revisions, photographs.

* HAZ-11: a hazard can be several kinds and have several controls picked from
  HSE's lists — `hazard_types` and `hazard_controls`, snapshots [{id, label}]
  as on a risk assessment. The single kind each hazard had is copied in.
  `control_level` stays: the PRD asks for both.
* Kirk, round 12: "if the form is revised, add a revision date … last revised
  on" — `revised_at` / `revised_by_name`, stamped by an edit and not by an
  assessment.
* HAZ-02: a hazard is now scored on the incident's form, where `controls` is
  "Hazard after controls are implemented". On the old hazard form the same
  column held the controls in place. Those are moved to `controls_in_place`
  so they are not read as what remains.
* HAZ-03: photographs on hazards, JSAs and permits — one table for all three
  (`ehs_record_attachments`), owner by type and id.

★ Prints what it moved; compare with the dev database (28 assessments with
controls text, 18 hazards with a kind on 11-10).

Revision ID: 20261110_0094
Revises: 20261109_0093
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261110_0094"
down_revision = "20261109_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("hazard_types", "hazard_controls"):
        op.add_column("ehs_hazards", sa.Column(col, JSONB, nullable=False, server_default="[]"))
    op.add_column("ehs_hazards", sa.Column("revised_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_hazards", sa.Column("revised_by_name", sa.String(255), nullable=True))
    op.add_column("ehs_risk_assessments", sa.Column("controls_in_place", sa.Text, nullable=True))

    conn = op.get_bind()
    kinds = conn.execute(sa.text("""
        update ehs_hazards
           set hazard_types = jsonb_build_array(jsonb_build_object(
                   'id', hazard_type_id::text, 'label', coalesce(hazard_type_label, '')))
         where hazard_type_id is not null""")).rowcount
    moved = conn.execute(sa.text("""
        update ehs_risk_assessments
           set controls_in_place = controls, controls = null
         where source_type = 'hazard' and controls is not null""")).rowcount
    print(f"hazards given their kind as a list: {kinds}; "
          f"hazard assessments whose controls text moved to controls_in_place: {moved}")

    op.create_table(
        "ehs_record_attachments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_type", sa.String(20), nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_ehs_record_attachments_owner", "ehs_record_attachments",
                    ["owner_type", "owner_id"])


def downgrade() -> None:
    op.drop_index("ix_ehs_record_attachments_owner", "ehs_record_attachments")
    op.drop_table("ehs_record_attachments")
    conn = op.get_bind()
    conn.execute(sa.text("""
        update ehs_risk_assessments set controls = controls_in_place
         where source_type = 'hazard' and controls_in_place is not null and controls is null"""))
    op.drop_column("ehs_risk_assessments", "controls_in_place")
    for col in ("revised_by_name", "revised_at", "hazard_controls", "hazard_types"):
        op.drop_column("ehs_hazards", col)
