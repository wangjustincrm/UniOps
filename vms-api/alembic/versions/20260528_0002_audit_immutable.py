"""Make vms_audit_logs DB-level immutable (VMS-AU-003).

Revokes UPDATE and DELETE on `vms_audit_logs` from the `epms` application
role (the role every UniOps service connects as). INSERT and SELECT remain
allowed. This makes audit log tampering require superuser access, satisfying
the "Write-Once, Read-Many" P0 requirement in VMS PRD §2.5.2.

Idempotent: REVOKE on a permission the role never had is a no-op.

Note: in local dev, `reset-db.sh` drops and recreates the database, which
re-grants ownership and effectively resets this. That's fine for dev; the
guarantee matters in production.

Revision ID: 20260528_0002
Revises: 20260528_0001
Create Date: 2026-05-28
"""
from alembic import op

revision = "20260528_0002"
down_revision = "20260528_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The application role is the user defined in POSTGRES_USER (default `epms`).
    # If you change the role in production, update this migration accordingly.
    op.execute("REVOKE UPDATE, DELETE ON vms_audit_logs FROM epms")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON vms_audit_logs TO epms")
