"""Work permits — confined space, hot work, height, lockout, excavation, electrical.

The jobs that kill people, and the permit is the control. Three shapes here
come from how permits fail rather than from how the form is drawn.

`valid_to` is indexed and the status is reconciled by the sweep, but the
effective status is computed on every read (app/services/permit.py). "The board
still says ACTIVE" is how a crew re-enters a space on yesterday's
authorisation, and a stored column alone cannot prevent that.

Gas readings carry their own computed verdict and the list of what failed. The
limits are the standard's — Reg 632 oxygen 19.5–23%, 10% LEL, Reg 833 exposure
limits — and are deliberately not configurable. An employer may add
precautions; an employer may not decide 18.9% oxygen is acceptable today.

Signatures are per capacity and per phase. A confined-space entry needs the
issuer, the receiver and the attendant separately, and closing is a different
act by a different person; one signature column would collapse three legal
responsibilities into "somebody signed".

Seeds the six permit types with their precaution lists.

Revision ID: 20260901_0022
Revises: 20260901_0021
Create Date: 2026-09-01
"""
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260901_0022"
down_revision = "20260901_0021"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def _p(key, label):
    return {"key": key, "label": label}


# (code, name, precautions, gas test, signatures, max hours)
_TYPES = [
    ("CONFINED_SPACE", "Confined space entry", [
        _p("plan", "Entry plan reviewed with everyone entering"),
        _p("isolated", "All energy and product lines isolated and locked"),
        _p("purged", "Space purged, drained and ventilated"),
        _p("gas_tested", "Atmosphere tested and continuously monitored"),
        _p("attendant", "Attendant posted outside for the whole entry"),
        _p("rescue", "Rescue plan in place and rescue equipment on site"),
        _p("comms", "Means of communication between entrant and attendant"),
    ], True, ["issuer", "receiver", "attendant"], 8),
    ("HOT_WORK", "Hot work", [
        _p("combustibles", "Combustibles removed or protected within 11 m"),
        _p("gas_tested", "Area tested for flammable atmosphere"),
        _p("extinguisher", "Fire extinguisher within reach"),
        _p("fire_watch", "Fire watch posted during and 60 minutes after"),
        _p("openings", "Floor and wall openings covered"),
        _p("detection", "Fire detection isolated if it will be triggered"),
    ], True, ["issuer", "receiver", "fire_watch"], 8),
    ("WORKING_AT_HEIGHT", "Working at height", [
        _p("training", "Everyone working at height is trained and current"),
        _p("anchor", "Anchor points identified and rated"),
        _p("equipment", "Fall arrest equipment inspected before use"),
        _p("area_below", "Area below barricaded"),
        _p("rescue", "Suspension rescue plan in place"),
    ], False, ["issuer", "receiver"], 12),
    ("LOTO", "Lockout / tagout", [
        _p("sources", "All energy sources identified, including stored energy"),
        _p("locked", "Each source locked and tagged by every person working"),
        _p("dissipated", "Stored energy released or restrained"),
        _p("verified", "Zero energy verified by attempting a start"),
    ], False, ["issuer", "receiver"], 12),
    ("EXCAVATION", "Excavation", [
        _p("locates", "Underground services located and marked"),
        _p("soil", "Soil type assessed and support method chosen"),
        _p("access", "Ladder or ramp within 3 m of any worker"),
        _p("spoil", "Spoil kept at least 1 m from the edge"),
        _p("gas_tested", "Atmosphere tested where deeper than 1.2 m"),
    ], True, ["issuer", "receiver"], 12),
    ("ELECTRICAL", "Live electrical work", [
        _p("justified", "Working live is justified and documented"),
        _p("qualified", "Worker qualified for the voltage and task"),
        _p("boundaries", "Approach boundaries established and marked"),
        _p("ppe", "Arc-rated PPE selected for the incident energy"),
        _p("second_person", "Second person present and trained in rescue"),
    ], False, ["issuer", "receiver"], 8),
]


def upgrade() -> None:
    op.create_table(
        "ehs_permit_types",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("precautions", JSONB, nullable=False, server_default="[]"),
        sa.Column("requires_gas_test", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("required_signatures", JSONB, nullable=False, server_default="[]"),
        sa.Column("max_duration_hours", sa.Integer, nullable=False, server_default="12"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_permits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("permit_no", sa.String(20), nullable=False, unique=True),
        sa.Column("type_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_permit_types.id", ondelete="RESTRICT"),
                  nullable=True, index=True),
        sa.Column("type_label", sa.String(150), nullable=False),
        sa.Column("work_description", sa.Text, nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("jsa_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jsa.id", ondelete="SET NULL"), nullable=True),
        sa.Column("contractor_company", sa.String(200), nullable=True),
        sa.Column("requested_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("requested_by_name", sa.String(255), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft", index=True),
        sa.Column("precautions_confirmed", JSONB, nullable=False, server_default="{}"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issued_by_name", sa.String(255), nullable=True),
        sa.Column("suspended_reason", sa.String(300), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("closed_by_name", sa.String(255), nullable=True),
        sa.Column("closeout_note", sa.Text, nullable=True),
        sa.Column("qr_token", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_permit_gas_readings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("permit_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_permits.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taken_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("taken_by_name", sa.String(255), nullable=True),
        sa.Column("oxygen_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("lel_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("h2s_ppm", sa.Numeric(7, 2), nullable=True),
        sa.Column("co_ppm", sa.Numeric(7, 2), nullable=True),
        sa.Column("other_note", sa.String(200), nullable=True),
        # Computed from the standard's limits, never accepted from the client.
        sa.Column("is_acceptable", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("failed", JSONB, nullable=False, server_default="[]"),
        sa.Column("instrument", sa.String(100), nullable=True),
    )

    op.create_table(
        "ehs_permit_signatures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("permit_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_permits.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("phase", sa.String(10), nullable=False, server_default="issue"),
        sa.Column("capacity", sa.String(20), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("person_name", sa.String(255), nullable=False),
        sa.Column("person_role", sa.String(150), nullable=True),
        sa.Column("external_company", sa.String(200), nullable=True),
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("signed_on", sa.Date, nullable=True),
    )

    for code, name, precautions, gas, signatures, hours in _TYPES:
        op.execute(sa.text("""
            INSERT INTO ehs_permit_types
                (id, code, name, precautions, requires_gas_test,
                 required_signatures, max_duration_hours, is_active,
                 created_at, updated_at)
            VALUES (gen_random_uuid(), :code, :name, CAST(:prec AS jsonb), :gas,
                    CAST(:sigs AS jsonb), :hours, true, now(), now())
            ON CONFLICT (code) DO NOTHING
        """).bindparams(code=code, name=name, prec=json.dumps(precautions),
                        gas=gas, sigs=json.dumps(signatures), hours=hours))


def downgrade() -> None:
    op.drop_table("ehs_permit_signatures")
    op.drop_table("ehs_permit_gas_readings")
    op.drop_table("ehs_permits")
    op.drop_table("ehs_permit_types")
