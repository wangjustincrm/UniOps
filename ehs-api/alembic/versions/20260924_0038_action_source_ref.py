"""Fill in the document every corrective action was raised from.

`source_ref` — "raised from INC-2026-0041" — is set by the few places in the
backend that pass it and by nothing else. It is not a field on `ActionCreate`,
so every action raised through the API, which is most of them, stored a null
one; the screens that offer to open the source had nothing to print and said
nothing at all.

`crud.action.create` now looks the number up when the caller does not supply
one. This fills in what is already there, from the same tables, so an action
raised last month reads the same as one raised today.

Actions whose source has since been deleted, and the kinds with no numbered
document behind them, keep their null: this repairs what can be repaired
rather than inventing a reference.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260924_0038" is 13 characters.
"""
from alembic import op

revision = "20260924_0038"
down_revision = "20260923_0037"
branch_labels = None
depends_on = None

# (source_type, table, number column)
SOURCES = [
    ("incident", "ehs_incidents", "incident_no"),
    ("inspection", "ehs_form_submissions", "submission_no"),
    ("permit", "ehs_permits", "permit_no"),
    ("hazard", "ehs_hazards", "hazard_no"),
    ("drill", "ehs_drills", "drill_no"),
    ("jhsc", "ehs_jhsc_meetings", "meeting_no"),
]


def upgrade() -> None:
    for source_type, table, column in SOURCES:
        op.execute(f"""
            update ehs_actions a
               set source_ref = s.{column}
              from {table} s
             where a.source_id = s.id
               and a.source_type = '{source_type}'
               and a.source_ref is null
        """)  # noqa: S608 — every value here is from the literal list above


def downgrade() -> None:
    # Nothing to undo that is worth undoing: the column is a snapshot of a
    # number that was already true, and blanking it again would only take
    # working links off screens that now have them.
    pass
