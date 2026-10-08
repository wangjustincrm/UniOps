"""Join the Safety branch's migrations to main's.

Safety: am03_config_single_row (company_config made single-row).
main:   ag09 .. as02_assistant_saved_q.

Both lines start from the same revision and touch nothing in common, so this
does no work of its own; it only gives alembic one head again. Production is
already on main's line and will run the Safety line, then this. Dev was on the
Safety line and will run main's, then this. Both orders were run on a copy of
the dev database before this was committed.

Revision ID: mg01_merge_main_and_ehs
Revises: "am03_config_single_row", "as02_assistant_saved_q"
"""
revision = "mg01_merge_main_and_ehs"
down_revision = ("am03_config_single_row", "as02_assistant_saved_q")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
