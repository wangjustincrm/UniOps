"""Filling and Can Infeed are places, not groupings — reshape so every place is a leaf.

The plant structure HSE supplied lists Building 103 as five siblings:

    Dryer · Filling · Filling – Blending · Can Infeed · Can Infeed – Buffer Room

0018 read the en-dash as containment and nested Blending under Filling, and
Buffer Room under Can Infeed. That is defensible as a tree, but it demotes
`Filling` and `Can Infeed` to interior nodes — and incident reporting now only
offers leaves, so two areas HSE named as reportable places became unselectable.

Building 102 is deliberately left alone: HSE's list has no bare "Pretreatment"
entry, only `Pretreatment – <line>`, so `Pretreatment` really is a grouping and
staying unselectable is correct. The asymmetry mirrors the business, not an
oversight.

Renames carry the full HSE wording, because once a node is a sibling rather
than a child, the parent no longer supplies that context in the path.

Revision ID: 0019_location_leaf_fix
Revises: 0018_locations
Create Date: 2026-08-31
"""
from alembic import op

revision = "0019_location_leaf_fix"
down_revision = "0018_locations"
branch_labels = None
depends_on = None

# (code, new_parent_code, new_name, new_path_tail, old_parent_code, old_name)
_MOVES = [
    ("103-FILL-BLEND", "103", "Filling – Blending", "103-FILL", "Blending"),
    ("103-CAN-BUFFER", "103", "Can Infeed – Buffer Room", "103-CAN", "Buffer Room"),
]


def _reparent(code: str, parent_code: str, name: str) -> None:
    # depth/path are denormalised, so they have to move with the row. Both nodes
    # go from depth 3 to depth 2; no grandchildren exist to cascade to.
    op.execute(
        f"""
        update locations child
           set parent_id = parent.id,
               name      = '{name.replace("'", "''")}',
               depth     = parent.depth + 1,
               path      = parent.path || '/' || '{name.replace("'", "''")}',
               updated_at = now()
          from locations parent
         where child.code = '{code}' and parent.code = '{parent_code}'
        """
    )


def upgrade() -> None:
    for code, new_parent, new_name, _old_parent, _old_name in _MOVES:
        _reparent(code, new_parent, new_name)


def downgrade() -> None:
    for code, _new_parent, _new_name, old_parent, old_name in _MOVES:
        _reparent(code, old_parent, old_name)
