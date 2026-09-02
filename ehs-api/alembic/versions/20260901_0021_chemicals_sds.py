"""The chemical inventory and its safety data sheets — WHMIS 2015 / Reg 860.

The regulation sets the shape. An employer keeps an inventory of the hazardous
products at the workplace, holds a current SDS for each, makes that sheet
available to workers, and does not let one go three years without review.

Two tables because a product and its data sheet have different lifetimes. The
drum of caustic in the CIP room is the same drum when the supplier issues a
revised sheet; the sheet is superseded, the product is not.

`hazard_classes` is a JSONB list of {id, label} rather than one column. A
product is routinely several things at once — corrosive and toxic — and a
single field forces whoever enters it to pick the worst and drop the rest,
which is exactly the information a first aider needs.

The three-year review goes into ehs_statutory_deadlines with the MOL and WSIB
clocks, so it is chased by the same sweep and appears on the same compliance
calendar. It runs from the supplier's revision date, not from the day the sheet
was filed.

Also seeds the two vocabularies this needs and Settings did not have: the nine
GHS hazard classes and the WHMIS 2015 classes.

Revision ID: 20260901_0021
Revises: 20260901_0020
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260901_0021"
down_revision = "20260901_0020"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)

# The GHS pictogram set, which is what appears on a workplace label.
_GHS = [
    ("EXPLOSIVE", "Explosive"),
    ("FLAMMABLE", "Flammable"),
    ("OXIDIZING", "Oxidizing"),
    ("COMPRESSED_GAS", "Gas under pressure"),
    ("CORROSIVE", "Corrosive"),
    ("TOXIC", "Acute toxicity"),
    ("HARMFUL", "Harmful / irritant"),
    ("HEALTH_HAZARD", "Serious health hazard"),
    ("ENVIRONMENT", "Hazardous to the environment"),
]


def upgrade() -> None:
    op.create_table(
        "ehs_chemicals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chemical_no", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(250), nullable=False, index=True),
        sa.Column("product_name", sa.String(250), nullable=True),
        sa.Column("manufacturer", sa.String(200), nullable=True),
        sa.Column("supplier", sa.String(200), nullable=True),
        sa.Column("product_code", sa.String(80), nullable=True),
        sa.Column("hazard_classes", JSONB, nullable=False, server_default="[]"),
        sa.Column("signal_word", sa.String(10), nullable=True),
        sa.Column("physical_state", sa.String(20), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("storage_note", sa.String(300), nullable=True),
        sa.Column("container_size", sa.String(60), nullable=True),
        sa.Column("quantity_on_site", sa.Numeric(12, 2), nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("ppe_required", sa.Text, nullable=True),
        sa.Column("first_aid_note", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true", index=True),
        sa.Column("approved_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_name", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_sds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chemical_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_chemicals.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("revision", sa.String(40), nullable=True),
        sa.Column("revision_date", sa.Date, nullable=True),
        sa.Column("received_on", sa.Date, nullable=True),
        sa.Column("review_due_on", sa.Date, nullable=True, index=True),
        sa.Column("file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        # A flag rather than a date comparison: a supplier's revision date is
        # what they printed on it, which is not always later than the sheet it
        # replaces, and "which sheet do I hand a worker" must never depend on
        # parsing that correctly.
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true", index=True),
        sa.Column("qr_token", sa.String(64), nullable=True, unique=True),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    # ── Vocabularies ────────────────────────────────────────────────────────
    # Marked system-locked: these are the GHS and WHMIS standards, not the
    # plant's own lists, and renaming "Corrosive" locally would put the label
    # out of step with the pictogram printed beside it.
    for code, name in (("ghs_hazard_class", "GHS Hazard Class"),
                       ("whmis_class", "WHMIS 2015 Class")):
        op.execute(sa.text(
            "INSERT INTO ehs_vocabularies (code, name, is_hierarchical, is_system_locked) "
            "VALUES (:c, :n, false, true) ON CONFLICT (code) DO NOTHING"
        ).bindparams(c=code, n=name))

    for order, (code, label) in enumerate(_GHS, start=1):
        op.execute(sa.text("""
            INSERT INTO ehs_vocabulary_items
                (id, vocabulary_code, code, label, sort_order, is_active, path)
            VALUES (gen_random_uuid(), 'ghs_hazard_class', :code, :label, :sort, true, :label)
            ON CONFLICT (vocabulary_code, code) DO NOTHING
        """).bindparams(code=code, label=label, sort=order * 10))


def downgrade() -> None:
    op.execute("DELETE FROM ehs_vocabulary_items "
               "WHERE vocabulary_code IN ('ghs_hazard_class','whmis_class')")
    op.execute("DELETE FROM ehs_vocabularies "
               "WHERE code IN ('ghs_hazard_class','whmis_class')")
    op.drop_table("ehs_sds")
    op.drop_table("ehs_chemicals")
