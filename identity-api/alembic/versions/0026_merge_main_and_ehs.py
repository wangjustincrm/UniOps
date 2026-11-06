"""Join the Safety branch's migrations to main's again.

Safety: 0020_merge_main_and_ehs (the merge point from 8 October).
main:   0025_hr_role (everything since, Staff among it).

Both lines touch nothing in common, so this does no work of its own; it only
gives alembic one head. It was run on a copy of the dev3 database first.

Revision ID: 0026_merge_main_and_ehs
Revises: "0020_merge_main_and_ehs", "0025_hr_role"
"""
revision = "0026_merge_main_and_ehs"
down_revision = ("0020_merge_main_and_ehs", "0025_hr_role")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
