"""One obligation of a kind per record, enforced.

Three things now depend on it: the sweep finds the task it already raised by the
obligation's name, satisfying an obligation closes that same task, and the two
cleanups above group by it. All three were written against a rule that only code
habit was keeping true — every path that starts a clock happens to reuse the
existing row rather than add a second.

The day somebody adds a path that forgets to, the database says so here, instead
of two obligations quietly folding into one prompt and one of them going
unanswered. That failure would be silent and it would be a statutory one.

A record can still owe several *different* obligations: a critical injury owes
the Ministry a report and WSIB a Form 7, and those differ by `kind`.

★ If duplicates already exist this migration stops and names them, rather than
choosing one to keep. These rows are the compliance record — they carry who
discharged the obligation, when, and the file proving it — and a migration runs
unattended at deployment, so a row it dropped would be evidence nobody saw go.
Two rows for one obligation means somebody has to decide which is true; that is
not a decision to make in SQL at four in the morning.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260925_0043" is 13 characters.
"""
from alembic import op

revision = "20260925_0043"
down_revision = "20260925_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    rows = op.get_bind().exec_driver_sql("""
        select source_type, source_id, kind, count(*) as n,
               count(*) filter (where satisfied_at is not null) as discharged
          from ehs_statutory_deadlines
      group by 1, 2, 3
        having count(*) > 1
      order by n desc
    """).fetchall()

    if rows:
        listing = "\n".join(
            f"  {r.source_type} {r.source_id} {r.kind}: {r.n} rows"
            f" ({r.discharged} already discharged)" for r in rows)
        raise RuntimeError(
            "ehs_statutory_deadlines holds more than one row for the same "
            "obligation, and this migration will not choose between them — "
            "they carry the evidence that an obligation was discharged.\n"
            f"{listing}\n"
            "Reconcile these by hand, keeping the row whose evidence is the "
            "real one, then run the migration again.")

    op.create_unique_constraint(
        "uq_ehs_deadline_source_kind", "ehs_statutory_deadlines",
        ["source_type", "source_id", "kind"])


def downgrade() -> None:
    op.drop_constraint(
        "uq_ehs_deadline_source_kind", "ehs_statutory_deadlines", type_="unique")
