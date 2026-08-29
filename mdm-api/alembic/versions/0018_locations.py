"""The plant area tree — site, building, department, line.

Every incident, inspection, hazard and corrective action in the Safety module
is filed against a location, and it is the most reused piece of reference data
the module has. It lives in mdm rather than in ehs-api because it is genuine
master data: the maintenance system that eventually replaces the cMMs
prototype wants the same tree for its equipment, VMS wants it for access areas,
and MRP wants it for lines. Putting it in ehs-api would mean renaming a
foreign-keyed table the first time a second consumer appeared.

The Safety module owns the *maintenance UI* even so — the HSE Manager is the
person who actually keeps this current, and asking them to go find it in
Portal's Data Maintenance would mean it never gets updated. Data ownership and
screen location are deliberately different here.

`access_area` carries the GMP zoning as a plain varchar rather than reusing
VMS's `vms_access_area` enum. The enum conflates two orthogonal things — where
a place is, and what hygiene regime applies to it — and extending a PG enum in
place is a migration every consumer has to be ready for.

Seed content is the structure the HSE Manager supplied on 2026-08-28. Note
that it is not uniformly four levels deep: 102 has building/department/line
while 104 Packaging and 105 Warehouse stop at two. Whether every location must
reach the full depth is still open with them (PRD v0.3 question 6.7), so
nothing enforces a depth here.

Revision id length: alembic_version_mdm.version_num is varchar(32);
"0018_locations" is 14 characters.
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0018_locations"
down_revision = "0017_material_accounting_group"
branch_labels = None
depends_on = None

_NS = uuid.UUID("6f1b4d2e-8a3c-4f19-9d77-2b5e0c8a1f44")  # stable namespace for seed ids


def _id(code: str) -> str:
    """Deterministic id per code, so a re-run seeds the same rows."""
    return str(uuid.uuid5(_NS, code))


# (code, name, level, parent_code, access_area)
_SEED = [
    ("KGN", "Kingston Plant", "site", None, None),

    ("102", "Building 102", "building", "KGN", None),
    ("102-PRE", "Pretreatment", "department", "102", "production_non_gmp"),
    ("102-PRE-MILKREC", "Milk Receiving", "line", "102-PRE", "production_non_gmp"),
    ("102-PRE-RAWPAST", "Raw Milk Pasteurizer", "line", "102-PRE", "production_non_gmp"),
    ("102-PRE-DOSING", "Dosing and Tipping", "line", "102-PRE", "production_non_gmp"),
    ("102-PRE-PAST", "Pasteurizer", "line", "102-PRE", "production_non_gmp"),
    ("102-EVAP", "Evaporator", "department", "102", "production_non_gmp"),

    ("103", "Building 103", "building", "KGN", None),
    ("103-DRYER", "Dryer", "department", "103", "production_gmp"),
    ("103-FILL", "Filling", "department", "103", "production_gmp"),
    ("103-FILL-BLEND", "Blending", "line", "103-FILL", "production_gmp"),
    ("103-CAN", "Can Infeed", "department", "103", "production_gmp"),
    ("103-CAN-BUFFER", "Buffer Room", "line", "103-CAN", "production_gmp"),

    ("104", "Building 104", "building", "KGN", None),
    ("104-PACK", "Packaging", "department", "104", "production_gmp"),

    ("105", "Building 105", "building", "KGN", None),
    ("105-WH", "Warehouse", "department", "105", "warehouse"),

    ("106", "Building 106", "building", "KGN", None),
    ("106-UTIL", "Utilities", "department", "106", "production_non_gmp"),
    ("106-WWTP", "Waste Water Treatment (WWTP)", "department", "106", "production_non_gmp"),

    ("107", "Building 107", "building", "KGN", None),
    ("107-OFFICE", "Office", "department", "107", "office"),
]


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("level", sa.String(20), nullable=False),
        # Materialized path, so filtering a subtree is one LIKE rather than a
        # recursive CTE in every report.
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("access_area", sa.String(30), nullable=True),
        sa.Column("department_id", sa.UUID(), nullable=True),
        # Scanned in the plant to report an incident or open that area's
        # checklist without typing a location.
        sa.Column("qr_token", sa.String(64), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["locations.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_locations_parent_id", "locations", ["parent_id"])
    op.create_index("ix_locations_path", "locations", ["path"])
    op.create_index("ix_locations_is_active", "locations", ["is_active"])

    paths: dict[str, str] = {}
    depths: dict[str, int] = {}
    rows = []
    for code, name, level, parent_code, access_area in _SEED:
        if parent_code is None:
            path, depth = name, 0
        else:
            path, depth = f"{paths[parent_code]}/{name}", depths[parent_code] + 1
        paths[code], depths[code] = path, depth
        rows.append({
            "id": _id(code), "code": code, "name": name,
            "parent_id": _id(parent_code) if parent_code else None,
            "level": level, "path": path, "depth": depth,
            "access_area": access_area, "qr_token": f"L-{_id(code)[:12]}",
        })

    locations = sa.table(
        "locations",
        sa.column("id", sa.UUID()), sa.column("code", sa.String()),
        sa.column("name", sa.String()), sa.column("parent_id", sa.UUID()),
        sa.column("level", sa.String()), sa.column("path", sa.String()),
        sa.column("depth", sa.SmallInteger()), sa.column("access_area", sa.String()),
        sa.column("qr_token", sa.String()),
    )
    op.bulk_insert(locations, rows)


def downgrade() -> None:
    op.drop_index("ix_locations_is_active", table_name="locations")
    op.drop_index("ix_locations_path", table_name="locations")
    op.drop_index("ix_locations_parent_id", table_name="locations")
    op.drop_table("locations")
