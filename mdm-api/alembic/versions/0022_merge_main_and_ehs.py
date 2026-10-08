"""Join the Safety branch's migrations to main's.

Safety: 0018_locations .. 0021_location_qr (the plant area tree).
main:   0018_bom_line_batch_qty (bom_lines.qty_per_batch).

Both lines start from the same revision and touch nothing in common, so this
does no work of its own; it only gives alembic one head again. Production is
already on main's line and will run the Safety line, then this. Dev was on the
Safety line and will run main's, then this. Both orders were run on a copy of
the dev database before this was committed.

Revision ID: 0022_merge_main_and_ehs
Revises: "0021_location_qr", "0018_bom_line_batch_qty"
"""
revision = "0022_merge_main_and_ehs"
down_revision = ("0021_location_qr", "0018_bom_line_batch_qty")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
