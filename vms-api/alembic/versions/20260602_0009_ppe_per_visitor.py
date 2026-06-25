"""Reshape vms_visits.ppe_requested to a per-visitor list.

V2.4 PPE shipped with one PPE record per Visit. Operators pointed out that
multi-visitor groups need different sizes per person — a single record
can't capture that. The new shape:

    {
      "items": [
        {"visitor_id": "<uuid>", "clothing_size": "L", "footwear": "shoes",
         "shoe_size": "10", ...},
        {"visitor_id": "<uuid>", ...}
      ],
      "notes": "free-form, applies to the whole group"
    }

Column type stays JSONB — no DDL change needed. This migration only
backfills existing rows into the new shape (wrapping the single record
under the primary visitor's UUID).

Revision ID: 20260602_0009
Revises: 20260602_0008
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260602_0009"
down_revision = "20260602_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE vms_visits
        SET ppe_requested = jsonb_build_object(
          'items', jsonb_build_array(
            jsonb_build_object(
              'visitor_id',          visitor_id,
              'clothing_size',       ppe_requested->>'clothing_size',
              'clothing_size_other', ppe_requested->>'clothing_size_other',
              'footwear',            ppe_requested->>'footwear',
              'shoe_size',           ppe_requested->>'shoe_size',
              'shoe_size_other',     ppe_requested->>'shoe_size_other'
            )
          ),
          'notes', ppe_requested->>'notes'
        )
        WHERE ppe_requested IS NOT NULL
          AND ppe_requested ? 'clothing_size'
          AND NOT (ppe_requested ? 'items')
    """))


def downgrade() -> None:
    # Best-effort: unwrap the first item back to the v1 shape. Loses data
    # if multi-visitor PPE was actually used — accept that as the cost of
    # reverting a feature.
    op.execute(sa.text("""
        UPDATE vms_visits
        SET ppe_requested = jsonb_build_object(
          'clothing_size',       (ppe_requested->'items'->0)->>'clothing_size',
          'clothing_size_other', (ppe_requested->'items'->0)->>'clothing_size_other',
          'footwear',            (ppe_requested->'items'->0)->>'footwear',
          'shoe_size',           (ppe_requested->'items'->0)->>'shoe_size',
          'shoe_size_other',     (ppe_requested->'items'->0)->>'shoe_size_other',
          'notes',                ppe_requested->>'notes'
        )
        WHERE ppe_requested IS NOT NULL
          AND ppe_requested ? 'items'
    """))
