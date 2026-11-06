"""Join the Safety branch's migrations to main's again.

Safety: 0022_merge_main_and_ehs (the merge point from 8 October).
main:   0020_staff_apply_department_map (everything since, Staff among it).

Both lines touch nothing in common, so this does no work of its own; it only
gives alembic one head. It was run on a copy of the dev3 database first.

Revision ID: 0023_merge_main_and_ehs
Revises: "0022_merge_main_and_ehs", "0020_staff_apply_department_map"
"""
revision = "0023_merge_main_and_ehs"
down_revision = ("0022_merge_main_and_ehs", "0020_staff_apply_department_map")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
