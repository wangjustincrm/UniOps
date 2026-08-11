"""pickup-slip ref uniqueness applies only to ACTIVE slips

Whole-branch review (I3). ag04's partial unique index on
(agreement_id, slip_ref) excluded only NULL refs, not retired rows — so a
mis-keyed slip that was voided, or one AP rejected, burned its slip_ref
inside that agreement forever. Re-recording the same paper slip (design §3
lists "open ──录错作废──→ voided" as a normal path, and AP reject is the
whole point of the pending_ap_review gate) came back 409 "already recorded",
with no UI anywhere able to release the ref off a terminal row.

Narrowing the predicate to active statuses keeps the constraint doing the one
job it was added for — stopping the SAME live slip being entered twice (a
second person re-keying it, or a client retry after a timeout) — while
letting a corrected re-entry through.

⚠️ Kept byte-for-byte in sync with
app/models/agreement_slip.py::__table_args__: the test DB is built by
Base.metadata.create_all and never runs these migrations, so a predicate that
lives only here is a predicate no test can see.

Revision ID: ag05_slip_ref_uq_active   (23 chars — version_num is varchar(32))
Revises: ag04_pickup_slips
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "ag05_slip_ref_uq_active"
down_revision = "ag04_pickup_slips"
branch_labels = None
depends_on = None

_ACTIVE = "slip_ref IS NOT NULL AND status NOT IN ('voided', 'rejected')"
_ANY = "slip_ref IS NOT NULL"


def _recreate(where: str) -> None:
    # Same index name in both directions: this replaces ag04's index in place
    # rather than adding a second one, so nothing downstream has to learn a
    # new name.
    op.drop_index("uq_agr_slip_ref_per_agreement", table_name="agreement_pickup_slips")
    op.create_index(
        "uq_agr_slip_ref_per_agreement", "agreement_pickup_slips",
        ["agreement_id", "slip_ref"], unique=True,
        postgresql_where=sa.text(where))


def upgrade() -> None:
    _recreate(_ACTIVE)


def downgrade() -> None:
    # ⚠️ Not always reversible in practice: if a voided/rejected slip and a
    # live one now share a ref inside one agreement (exactly what upgrade()
    # exists to allow), recreating the wider index raises. That is correct —
    # it refuses to silently drop the uniqueness guarantee it claims to
    # restore. Resolve the duplicate refs first if this ever has to run.
    _recreate(_ANY)
