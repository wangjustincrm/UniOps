"""Equipment: System becomes a list HSE maintain, and machines can be retired.

Round 12, EQ-01/EQ-03 and Q1: "make System an editable list starting from
today's 148 values, and HSE merges them into the groups you want." The free
text `equipment_type` is seeded into a new `equipment_system` list, one entry
per distinct value, and every machine is pointed at its entry by id.

Q3: "a Retire button that hides it from pickers and keeps the history". The
retire flag already existed (`is_active`); this adds when and by whom.

★ Prints how many machines were linked against how many had a value. They must
be equal: a smaller number is machines silently left without a System.

Revision ID: 20261109_0088
Revises: 20261106_0087
"""
import re
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261109_0088"
down_revision = "20261106_0087"
branch_labels = None
depends_on = None

_NS = uuid.UUID("0c9a52e4-5b8e-4f0e-8d55-3f7f3a1c9e21")


def _code(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")[:50] or "ENTRY"
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    taken.add(code)
    return code


def upgrade() -> None:
    op.add_column("ehs_equipment", sa.Column(
        "system_id", UUID(as_uuid=True),
        sa.ForeignKey("ehs_vocabulary_items.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_ehs_equipment_system_id", "ehs_equipment", ["system_id"])
    op.add_column("ehs_equipment", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_equipment", sa.Column("retired_by_name", sa.String(255), nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text("""
        insert into ehs_vocabularies (code, name, description, is_hierarchical, is_system_locked)
        values ('equipment_system', 'Equipment System',
                'The System a machine belongs to, on the equipment register. '
                'Equipment pickers can be narrowed by it.',
                false, false)
        on conflict (code) do nothing
    """))
    labels = [r[0] for r in conn.execute(sa.text("""
        -- One entry per spelling ignoring case: "Valve" and "valve" are one System.
        select min(btrim(equipment_type)) from ehs_equipment
        where nullif(btrim(equipment_type), '') is not null
        group by lower(btrim(equipment_type))
        order by 1
    """))]
    taken = set(conn.execute(sa.text(
        "select code from ehs_vocabulary_items where vocabulary_code = 'equipment_system'"
    )).scalars())
    for sort, label in enumerate(labels, start=1):
        conn.execute(sa.text("""
            insert into ehs_vocabulary_items
                (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
            values (:id, 'equipment_system', null, :code, :label, :sort, true, '{}'::jsonb, :label)
            on conflict (vocabulary_code, code) do nothing
        """), {"id": uuid.uuid5(_NS, label.lower()), "code": _code(label[:200], taken),
               "label": label[:200], "sort": sort})

    linked = conn.execute(sa.text("""
        update ehs_equipment e set system_id = v.id
        from ehs_vocabulary_items v
        where v.vocabulary_code = 'equipment_system'
          and lower(v.label) = lower(btrim(e.equipment_type))
    """)).rowcount
    wanted = conn.execute(sa.text(
        "select count(*) from ehs_equipment where nullif(btrim(equipment_type), '') is not null"
    )).scalar()
    print(f"equipment_system: {len(labels)} systems, {linked} of {wanted} machines linked")
    if linked != wanted:
        raise RuntimeError(f"only {linked} of {wanted} machines were linked to a System")
    conn.execute(sa.text(
        "update ehs_equipment set retired_at = updated_at where not is_active and retired_at is null"))


def downgrade() -> None:
    op.drop_column("ehs_equipment", "retired_by_name")
    op.drop_column("ehs_equipment", "retired_at")
    op.drop_index("ix_ehs_equipment_system_id", table_name="ehs_equipment")
    op.drop_column("ehs_equipment", "system_id")
    op.execute("delete from ehs_vocabulary_items where vocabulary_code = 'equipment_system'")
    op.execute("delete from ehs_vocabularies where code = 'equipment_system'")
