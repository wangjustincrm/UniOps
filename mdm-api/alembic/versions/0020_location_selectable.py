"""Restructure the plant tree the way HSE asked, and make selectability a property.

Two changes, both from HSE's reply to PRD v0.4 (§4.2).

**Selectability becomes a column.** Until now the picker offered only childless
nodes, on the reasoning that recording an incident against a whole building is
worse than making somebody choose. HSE agreed with the goal and rejected the
mechanism: "I agree with making it a selectability property of the area rather
than a rule." The reason is concrete — once an operational area has Line 1 and
Line 2 beneath it, a leaf-only rule stops the area itself being selectable, and
a reporter who does not know which line it happened on is forced to guess. A
guessed line is worse than no line.

**Order becomes data.** HSE listed the general areas with Outside the Plant
and Production first — the two most often needed — and listed the eleven
operational areas in process order, from milk receiving through to packaging.
Sorting by name throws both away and puts the warehouse above production. So
`sort_order` is stored and the picker follows it.

**The tree is reorganised around what people know.** HSE: "employees may not
know the building numbers. Selecting the operational area by name is more
intuitive and reduces the chance of recording an incident against the wrong
location." So the top level is now six general areas rather than six buildings,
and the building number travels along inside the operational area's own name
where HSE gave one.

    Outside the Plant                          selectable, stops here
    Production                                 a heading, not selectable
      102 – Pretreatment – Milk Receiving      selectable, Line 1 / Line 2 optional
      … eleven operational areas …
    107 – Office                               selectable, stops here
    105 – Warehouse                            selectable, stops here
    106 – Utilities                            selectable, stops here
    Wastewater Treatment Plant (WWTP)          selectable, stops here

Production is the one node marked unselectable. HSE did not say so outright —
they said that choosing it leads to the next level — but "an incident happened
somewhere in Production" is precisely the recording this whole arrangement
exists to prevent, and it is the reading their own stated rationale supports.
Flagged to them rather than assumed silently.

The old rows are removed only where nothing points at them. Anything already
referenced by a Safety record is deactivated instead, so an existing incident
keeps the area it was filed against and no picker ever offers it again.

Revision ID: 0020_location_selectable
Revises: 0019_location_leaf_fix
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0020_location_selectable"
down_revision = "0019_location_leaf_fix"
branch_labels = None
depends_on = None

# Every Safety column that holds a location id. Checked before deleting an old
# row, so this migration is safe to run against a database that has been used.
_REFERENCING = [
    ("ehs_incidents", "location_id"),
    ("ehs_actions", "location_id"),
    ("ehs_equipment", "location_id"),
    ("ehs_first_aid_log", "location_id"),
    ("ehs_form_submissions", "location_id"),
    ("ehs_form_schedule_runs", "location_id"),
    ("ehs_hazards", "location_id"),
    ("ehs_jsa", "location_id"),
    ("ehs_safety_assets", "location_id"),
    ("ehs_course_deliveries", "location_id"),
    ("ehs_tbt_deliveries", "location_id"),
    ("ehs_worker_profiles", "primary_location_id"),
]

# (code, name) for the eleven operational areas, in HSE's order and wording.
_OPERATIONAL = [
    ("102-PRE-MILKREC", "102 – Pretreatment – Milk Receiving"),
    ("102-PRE-RAWPAST", "102 – Pretreatment – Raw Milk Pasteurizer"),
    ("102-PRE-DOSING", "102 – Pretreatment – Dosing and Tipping"),
    ("102-PRE-PAST", "102 – Pretreatment – Pasteurizer"),
    ("102-EVAP", "102 – Evaporator"),
    ("103-DRYER", "103 – Dryer"),
    ("103-FILL", "103 – Filling"),
    ("103-FILL-BLEND", "103 – Filling – Blending"),
    ("103-CAN", "103 – Can Infeed"),
    ("103-CAN-BUFFER", "103 – Can Infeed – Buffer Room"),
    ("104-PACK", "104 – Packaging"),
]

# (code, name, selectable) for the top level, in HSE's order.
_GENERAL = [
    ("OUTSIDE", "Outside the Plant", True),
    ("PROD", "Production", False),
    ("107-OFFICE", "107 – Office", True),
    ("105-WH", "105 – Warehouse", True),
    ("106-UTIL", "106 – Utilities", True),
    ("106-WWTP", "Wastewater Treatment Plant (WWTP)", True),
]


def upgrade() -> None:
    op.add_column("locations", sa.Column(
        "is_selectable", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("locations", sa.Column(
        "sort_order", sa.Integer(), nullable=False, server_default="0"))

    conn = op.get_bind()

    # ── Retire what is there, keeping anything already used ─────────────────
    used = set()
    for table, column in _REFERENCING:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"),
            {"t": table, "c": column}).scalar()
        if not exists:
            continue
        for (loc_id,) in conn.execute(sa.text(
            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608
        )).all():
            used.add(loc_id)

    if used:
        conn.execute(sa.text(
            "UPDATE locations SET is_active = false, is_selectable = false "
            "WHERE id = ANY(:ids)"), {"ids": list(used)})
        conn.execute(sa.text(
            "DELETE FROM locations WHERE NOT (id = ANY(:ids))"), {"ids": list(used)})
    else:
        conn.execute(sa.text("DELETE FROM locations"))

    # ── Build the new tree ──────────────────────────────────────────────────
    def insert(code, name, level, parent_id, path, depth, selectable, order):
        return conn.execute(sa.text("""
            INSERT INTO locations
                (id, code, name, parent_id, level, path, depth,
                 is_active, is_selectable, sort_order, created_at, updated_at)
            VALUES
                (gen_random_uuid(), :code, :name, :parent, :level, :path, :depth,
                 true, :selectable, :order, now(), now())
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name, parent_id = EXCLUDED.parent_id,
                level = EXCLUDED.level, path = EXCLUDED.path, depth = EXCLUDED.depth,
                is_active = true, is_selectable = EXCLUDED.is_selectable,
                sort_order = EXCLUDED.sort_order, updated_at = now()
            RETURNING id
        """), {"code": code, "name": name, "parent": parent_id, "level": level,
               "path": path, "depth": depth, "selectable": selectable,
               "order": order}).scalar()

    production_id = None
    for order, (code, name, selectable) in enumerate(_GENERAL, start=1):
        node_id = insert(code, name, "area", None, name, 0, selectable, order * 10)
        if code == "PROD":
            production_id = node_id

    for order, (code, name) in enumerate(_OPERATIONAL, start=1):
        area_id = insert(code, name, "department", production_id,
                         f"Production/{name}", 1, True, order * 10)
        # HSE: "Each of these operational areas has Line 1 and Line 2." Both
        # selectable, and neither compulsory — the area above stays selectable
        # so a reporter who does not know the line is not forced to guess.
        for line_no, line in enumerate(("Line 1", "Line 2"), start=1):
            insert(f"{code}-{line.replace(' ', '').upper()}", line, "line",
                   area_id, f"Production/{name}/{line}", 2, True, line_no * 10)


def downgrade() -> None:
    op.drop_column("locations", "sort_order")
    op.drop_column("locations", "is_selectable")
