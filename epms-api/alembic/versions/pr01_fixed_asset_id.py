"""Give Type 5 PRs somewhere to put the Fixed Asset ID.

The Create PR form has asked for a Fixed Asset ID — with a required asterisk —
since it was written, and every value ever typed into it was discarded: the
field was never in the submit payload, and there was no column to receive it.
PRD §2 has listed "Requires fixed asset ID" for Type 5 the whole time.

Nullable, because every Type 5 PR that already exists has no value and must stay
readable, approvable and payable. The requirement is enforced at submit only,
next to the vendor and budget gates in services/doc_preflight.py.

Revision ID: pr01_fixed_asset_id
Revises: as01_assistant_usage
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "pr01_fixed_asset_id"
down_revision: Union[str, None] = "as01_assistant_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests",
        sa.Column("fixed_asset_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_requests", "fixed_asset_id")
