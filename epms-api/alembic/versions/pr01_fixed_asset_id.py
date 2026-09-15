"""Give Type 5 PRs somewhere to put the Fixed Asset ID.

The Create PR form has asked for a Fixed Asset ID — with a required asterisk —
since it was written, and every value ever typed into it was discarded: the
field was never in the submit payload, and there was no column to receive it.
PRD §2 has listed "Requires fixed asset ID" for Type 5 the whole time.

Nullable, because every Type 5 PR that already exists has no value and must stay
readable, approvable and payable. The requirement is enforced at submit only,
next to the vendor and budget gates in services/doc_preflight.py.

Revision ID: pr01_fixed_asset_id
Revises: po01_pr_owner_id

Chained behind po01_pr_owner_id rather than off as01_assistant_usage, where it
was written. Both branches added a column to purchase_requests and both pointed
at the same parent, which gives alembic two heads — and `alembic upgrade head`
(what migrate-prod.sh runs, under `set -e`) refuses to choose between them. The
production run would have stopped at epms-api and taken identity-api,
approval-api and everything after it down with it.

The two are independent ADD COLUMNs, so the order between them carries no
meaning; what matters is that there is one.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "pr01_fixed_asset_id"
down_revision: Union[str, None] = "po01_pr_owner_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests",
        sa.Column("fixed_asset_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_requests", "fixed_asset_id")
