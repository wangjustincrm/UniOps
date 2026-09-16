"""Give every plant area a scannable token.

0018 issued one to each area it seeded. 0020 rebuilt the tree to HSE's layout —
six general areas, eleven operational areas, the lines beneath them — and did
not, so every area ended up with the column empty. Nothing complained: the
column is nullable, the badge that says "has a scannable code" simply stopped
appearing, and a missing sticker looks the same as one nobody has printed yet.

The sticker by a door is the one thing on this module a person uses without
being asked to. Scanning it opens the incident form with the area already
filled in, which is the difference between reporting something on the way past
and meaning to report it later.

Same shape as 0018's, so anything printed from that generation still resolves.

Revision ID: 0021_location_qr
Revises: 0020_location_selectable
Create Date: 2026-09-16
"""
from alembic import op

revision = "0021_location_qr"
down_revision = "0020_location_selectable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only the empty ones: an area carrying a token from 0018 keeps it, or a
    # sticker already on a wall would stop working.
    op.execute("""
        UPDATE locations
           SET qr_token = 'L-' || left(replace(id::text, '-', ''), 12)
         WHERE qr_token IS NULL
    """)


def downgrade() -> None:
    # Not reversible: which tokens this filled in is not recorded, and clearing
    # them all would take out the ones 0018 issued.
    pass
