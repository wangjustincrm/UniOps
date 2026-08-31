"""company_config is a single-row table that had no way of saying so.

`config_crud.get_or_create` reads with `select(CompanyConfig).limit(1)` and
inserts when that comes back empty. Nothing serialises the two halves, so on a
cold database every service that boots at once races: dev3 ended up with three
rows, two of them created 0.01s apart during the initial `docker compose up`.

The read has no ORDER BY, so which row wins is whatever the heap returns —
stable in practice, until a row is updated and moves. The visible symptom is a
setting that will not stick: an admin edits the branding, the write lands on
the row the UI loaded, and a later read picks up a different one. The Safety
tagline added alongside this migration would have been unsettable for exactly
that reason.

Dedupe keeps the row that was actually configured — most `module_taglines`
entries, then most recently updated — and the unique index makes the invariant
structural, so a future race fails loudly at INSERT instead of silently
forking the config. `((true))` is the standard single-row expression index: a
constant expression is identical for every row, so a second INSERT conflicts.

Nothing references `company_config.id`, so deleting the losers is safe; the
guard below still asserts that before it runs.

Revision ID: am03_config_single_row
Revises: am02_po_signoff
Create Date: 2026-08-31
"""
from alembic import op

revision = "am03_config_single_row"
down_revision = "am02_po_signoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Refuse to delete if some table started referencing config rows since this
    # was written — losing a referenced row is worse than a failed migration.
    referencing = conn.exec_driver_sql("""
        select count(*) from pg_constraint
         where confrelid = 'company_config'::regclass and contype = 'f'
    """).scalar()
    if referencing:
        raise RuntimeError(
            f"{referencing} foreign key(s) now point at company_config; "
            "dedupe by hand rather than letting this migration delete rows."
        )

    op.execute("""
        delete from company_config
         where id not in (
             select id from company_config
              order by coalesce(jsonb_array_length(
                          coalesce(jsonb_path_query_array(module_taglines, '$.keyvalue()'),
                                   '[]'::jsonb)), 0) desc,
                       updated_at desc nulls last,
                       id
              limit 1
         )
    """)
    # A constant expression index — alembic's create_index cannot express one,
    # so this goes through raw DDL.
    op.execute(
        "create unique index uq_company_config_single_row on company_config ((true))"
    )


def downgrade() -> None:
    op.drop_index("uq_company_config_single_row", table_name="company_config")
