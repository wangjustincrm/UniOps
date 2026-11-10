"""The Food Safety incident category, for the food-safety near miss.

Round 12, INC-01 and REP-01: "Near Miss - Food Safety" belongs to a Food Safety
category on Reports. The category existed only on the test database, typed in
by hand; without it here the new type would resolve to no category, silently,
on every other database.

Revision ID: 20261109_0089
Revises: 20261109_0088
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261109_0089"
down_revision = "20261109_0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    sort = conn.execute(sa.text(
        "select coalesce(max(sort_order), 0) + 1 from ehs_vocabulary_items"
        " where vocabulary_code = 'incident_category'")).scalar()
    added = conn.execute(sa.text("""
        insert into ehs_vocabulary_items
            (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
        values (:id, 'incident_category', null, 'FOOD', 'Food Safety', :sort, true,
                '{}'::jsonb, 'Food Safety')
        on conflict (vocabulary_code, code) do nothing
    """), {"id": uuid.uuid5(uuid.NAMESPACE_URL, "ehs/incident_category/FOOD"),
           "sort": sort}).rowcount
    print(f"incident_category FOOD: {'added' if added else 'already there'}")


def downgrade() -> None:
    # Left in place: an incident may name it by then, and entries are retired,
    # never deleted.
    pass
