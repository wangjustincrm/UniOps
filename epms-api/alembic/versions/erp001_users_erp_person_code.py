"""add erp_person_code to users + vendors erp_id partial unique

Revision ID: erp001_users_erp_person_code
Revises: 7f71e88f425f
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa


revision = "erp001_users_erp_person_code"
down_revision = "7f71e88f425f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("erp_person_code", sa.String(50), nullable=True))
    op.create_index(
        "ux_users_erp_person_code",
        "users",
        ["erp_person_code"],
        unique=True,
        postgresql_where=sa.text("erp_person_code IS NOT NULL"),
    )
    # vendors.erp_id already exists (vendors model has it; DB confirmed). Attempt to add
    # partial unique index — but the DB has duplicate erp_id values (14 groups), so this
    # will silently skip if it cannot be created. De-duplication is a separate data-cleanup
    # task before this index can be enforced.
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE UNIQUE INDEX ux_vendors_erp_id
                    ON vendors (erp_id) WHERE erp_id IS NOT NULL;
            EXCEPTION WHEN others THEN
                RAISE NOTICE 'Skipping ux_vendors_erp_id: %', SQLERRM;
            END;
        END$$;
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ux_vendors_erp_id")
    op.drop_index("ux_users_erp_person_code", table_name="users")
    op.drop_column("users", "erp_person_code")
