"""HSE's JHSC recommendation workflow.

Round 12 (UAT 12, the JHSC question): eleven steps from recommendation to HSE
verifying it in the JHSC register. The states on HSE's list become:

    New request                 → Being reviewed         (code new_request)
    Under review by maintenance → Under Review           (code maintenance_review)
    Work in progress              unchanged              (code in_progress)
    (new) Awaiting HSE verification                      (code hse_verification)
    Completed                     unchanged              (code completed)

Codes are kept — the code is what the workflow moves by — and a label is only
renamed where HSE have not already renamed it themselves. Recommendations
showing an old label are given the new one: this is the state it is in now,
not words it was signed with.

Adds when the person it was sent to first opened it, and when HSE verified it.

★ Prints what it changed; check the numbers against the dev database before
trusting it elsewhere.

Revision ID: 20261109_0092
Revises: 20261109_0091
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261109_0092"
down_revision = "20261109_0091"
branch_labels = None
depends_on = None

_NS = uuid.UUID("6f3b7c3a-1d6e-4c55-9b9e-0b5f0f1c2a69")  # as 0078
_RENAMES = [("new_request", "New request", "Being reviewed"),
            ("maintenance_review", "Under review by maintenance", "Under Review")]


def upgrade() -> None:
    for col in ("reviewed_at", "hse_verified_at"):
        op.add_column("ehs_jhsc_recommendations",
                      sa.Column(col, sa.DateTime(timezone=True), nullable=True))
    for col in ("reviewed_by_name", "hse_verified_by_name"):
        op.add_column("ehs_jhsc_recommendations", sa.Column(col, sa.String(255), nullable=True))

    conn = op.get_bind()
    for code, old, new in _RENAMES:
        items = conn.execute(sa.text("""
            update ehs_vocabulary_items set label = :new, path = :new
            where vocabulary_code = 'jhsc_rec_status' and code = :code and label = :old
            returning id"""), {"code": code, "old": old, "new": new}).scalars().all()
        recs = conn.execute(sa.text("""
            update ehs_jhsc_recommendations set status_label = :new
            where status_item_id = any(:ids) and status_label = :old"""),
            {"ids": list(items), "old": old, "new": new}).rowcount if items else 0
        print(f"jhsc_rec_status {code}: {'renamed' if items else 'left as HSE named it'}; "
              f"{recs} recommendations relabelled")

    sort = conn.execute(sa.text(
        "select sort_order from ehs_vocabulary_items"
        " where vocabulary_code = 'jhsc_rec_status' and code = 'completed'")).scalar()
    if sort is not None:
        conn.execute(sa.text("""
            update ehs_vocabulary_items set sort_order = sort_order + 1
            where vocabulary_code = 'jhsc_rec_status' and sort_order >= :s"""), {"s": sort})
    added = conn.execute(sa.text("""
        insert into ehs_vocabulary_items
            (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
        values (:id, 'jhsc_rec_status', null, 'hse_verification', 'Awaiting HSE verification',
                :sort, true, '{}'::jsonb, 'Awaiting HSE verification')
        on conflict (vocabulary_code, code) do nothing"""),
        {"id": uuid.uuid5(_NS, "jhsc_rec_status:hse_verification"),
         "sort": sort if sort is not None else 99}).rowcount
    print(f"jhsc_rec_status hse_verification: {'added' if added else 'already there'}")


def downgrade() -> None:
    for col in ("hse_verified_by_name", "reviewed_by_name", "hse_verified_at", "reviewed_at"):
        op.drop_column("ehs_jhsc_recommendations", col)
