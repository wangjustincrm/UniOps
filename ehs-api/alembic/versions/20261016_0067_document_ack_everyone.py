"""Everybody signs a document only when somebody said so.

Round 7, TC-11: "make the who has to sign a mandatory field." An empty list of
roles and people meant the whole plant, so a field left blank put a procedure
on everybody's list. `ack_everyone` makes that a choice; publishing refuses a
document that has to be signed and says nobody. Every existing document with
an empty list is marked as everybody — what it already meant — so nothing
already published or drafted changes audience.

Revision ID: 20261016_0067
Revises: 20261016_0066
"""
import sqlalchemy as sa
from alembic import op

revision = "20261016_0067"
down_revision = "20261016_0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_documents", sa.Column(
        "ack_everyone", sa.Boolean, nullable=False, server_default=sa.false()))
    op.execute("""
        UPDATE ehs_documents SET ack_everyone = true
        WHERE coalesce(jsonb_array_length(ack_roles), 0) = 0
          AND coalesce(jsonb_array_length(ack_user_ids), 0) = 0
    """)


def downgrade() -> None:
    op.drop_column("ehs_documents", "ack_everyone")
