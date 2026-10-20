"""The risk assessment HSE described in round 8.

Round 7 (open box) and round 8, question 5: the hazard picked from the hazard
list; "Judged on" a list HSE keeps under Settings → Lists, with Equipment
added; the hierarchy of controls and the specific hazard controls, any number
of each; then how likely and how bad once those are in place — the final
assessment.

- `consequence_type`, a vocabulary ("Judged on"). Each item carries the
  matrix's severity wording for that consequence in attrs.severity, level →
  text, copied from ehs_risk_severity for the five already there, so there is
  one place it is kept from now on. Equipment starts with none: HSE enters it,
  and until then it is not offered (a band with no wording cannot be scored).
- ehs_risk_assessments: hazard_item_id (the hazard list entry), and
  hierarchy_controls / hazard_controls, each a snapshot [{id, label}] so a
  later rename in the list does not rewrite an assessment.

Revision ID: 20261020_0069
Revises: 20261020_0068
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261020_0069"
down_revision = "20261020_0068"
branch_labels = None
depends_on = None

_NS = uuid.UUID("6f3b7c3a-1d6e-4c55-9b9e-0b5f0f1c2a69")
_TYPES = [
    ("people", "People"), ("downtime", "Unplanned downtime"), ("reputation", "Reputation"),
    ("environmental", "Environmental"), ("asset_damage", "Asset damage"), ("equipment", "Equipment"),
]


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        insert into ehs_vocabularies (code, name, description, is_hierarchical, is_system_locked)
        values ('consequence_type', 'Judged on',
                'What a risk assessment is judged on. Each entry carries the matrix''s wording '
                'for every level of how bad; an entry with no wording is not offered.', false, false)
        on conflict (code) do nothing
    """))
    # The five the severity table has a column for; Equipment has none.
    columns = {"people", "downtime", "reputation", "environmental", "asset_damage"}
    rows = conn.execute(sa.text(
        "select level, people, downtime, reputation, environmental, asset_damage "
        "from ehs_risk_severity order by level")).mappings().all()
    for sort, (code, label) in enumerate(_TYPES, start=1):
        severity = ({str(r["level"]): r[code] for r in rows if r[code]} if code in columns else {})
        conn.execute(sa.text("""
            insert into ehs_vocabulary_items
                (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
            values (:id, 'consequence_type', null, :code, :label, :sort, true,
                    cast(:attrs as jsonb), :path)
            on conflict (vocabulary_code, code) do nothing
        """), {"id": uuid.uuid5(_NS, f"consequence_type:{code}"), "code": code, "label": label,
               "sort": sort, "attrs": json.dumps({"severity": severity}),
               "path": label})

    op.add_column("ehs_risk_assessments", sa.Column("hazard_item_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_risk_assessments", sa.Column(
        "hierarchy_controls", JSONB, nullable=False, server_default="[]"))
    op.add_column("ehs_risk_assessments", sa.Column(
        "hazard_controls", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("ehs_risk_assessments", "hazard_controls")
    op.drop_column("ehs_risk_assessments", "hierarchy_controls")
    op.drop_column("ehs_risk_assessments", "hazard_item_id")
    op.execute("delete from ehs_vocabulary_items where vocabulary_code = 'consequence_type'")
    op.execute("delete from ehs_vocabularies where code = 'consequence_type'")
