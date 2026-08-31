"""HSE's risk matrix, as they actually use it.

Their document (HSE RA ver 1.0, K Smith, Dec 2024) is not the usual 5x5 of
likelihood against a single severity. Severity is judged across five different
consequence types — people, unplanned downtime, reputation, environmental, asset
damage — each with its own description at every band. An assessment therefore
has to record WHICH consequence type was used, or "severity 3" means five
different things and none of them are recoverable from the number.

Levels are seeded rather than hardcoded because the matrix is a company
document: HSE revises it, and a revision must not require a deployment. The
bands themselves stay as data; what is fixed is that there are two axes and a
rating table joining them.

The rating grid is stored on the likelihood row as `ratings`, keyed by severity
level: {"0":"acceptable","1":"acceptable",...}. A 5x6 grid is 30 values, and a
join table for 30 rows that are always read together would cost a query per
lookup and buy nothing.

Assessments snapshot the labels they were made under, per the module's rule:
the matrix will be revised, and a signed assessment has to keep reading the way
it was signed.

Revision ID: 20260831_0012
Revises: 20260831_0011
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260831_0012"
down_revision = "20260831_0011"
branch_labels = None
depends_on = None

# Straight from the PDF. Frequencies included because they are what make the
# bands mean the same thing to two different assessors.
_LIKELIHOOD = [
    ("A", "Virtually Impossible", "Once in 1000 yrs", "1.0E-03/yr",
     {"0": "acceptable", "1": "acceptable", "2": "acceptable", "3": "acceptable",
      "4": "acceptable", "5": "moderate"}),
    ("B", "Only Slightly Possible", "Once in 100 yrs", "1.0E-02/yr",
     {"0": "acceptable", "1": "acceptable", "2": "acceptable", "3": "acceptable",
      "4": "moderate", "5": "serious"}),
    ("C", "Possible", "Once in 10 years", "1.0E-01/yr",
     {"0": "acceptable", "1": "acceptable", "2": "acceptable", "3": "moderate",
      "4": "serious", "5": "critical"}),
    ("D", "Predicted", "Once per year", "2.0E-01/yr",
     {"0": "acceptable", "1": "acceptable", "2": "moderate", "3": "serious",
      "4": "critical", "5": "critical"}),
    ("E", "Expected", "Once per month", "1.0E+00/yr",
     {"0": "acceptable", "1": "moderate", "2": "serious", "3": "serious",
      "4": "critical", "5": "critical"}),
]

# (level, people, downtime, reputation)
_SEVERITY = [
    (0, "No injury or illness; near miss", "No downtime", "No impact"),
    (1, "First aid", "Less than one hour", "Negative public complaint"),
    (2, "Medical aid and/or modified work", "Restricted / less than one shift",
     "Little or no local media coverage"),
    (3, "Lost time illness (LTI)", "1 shift to 3 days",
     "Local media coverage, community complaint"),
    (4, "Hospitalization", "3 days to 10 days",
     "Negative national or regional publicity, transitory impact on valuation"),
    (5, "Fatality or permanent disability", "10 days to 90 days",
     "Negative publicity, significant impact on market share or investor valuation"),
]


def upgrade() -> None:
    op.create_table(
        "ehs_risk_likelihood",
        sa.Column("code", sa.String(4), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("qualitative", sa.String(200), nullable=True),
        sa.Column("frequency", sa.String(40), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        # severity level -> rating, because the grid is always read whole.
        sa.Column("ratings", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "ehs_risk_severity",
        sa.Column("level", sa.SmallInteger, primary_key=True),
        sa.Column("people", sa.String(300), nullable=True),
        sa.Column("downtime", sa.String(300), nullable=True),
        sa.Column("reputation", sa.String(300), nullable=True),
        sa.Column("environmental", sa.String(300), nullable=True),
        sa.Column("asset_damage", sa.String(300), nullable=True),
    )

    op.create_table(
        "ehs_risk_assessments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # Polymorphic, like corrective actions: an assessment hangs off an
        # incident, a JSA, a permit or nothing at all.
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("source_id", UUID(as_uuid=True), nullable=True),
        sa.Column("hazard_id", UUID(as_uuid=True), nullable=True),
        sa.Column("hazard_label", sa.String(200), nullable=True),
        sa.Column("description", sa.Text, nullable=True),

        # Which of the five consequence types this severity was judged on —
        # without it the number is not interpretable.
        sa.Column("consequence_type", sa.String(20), nullable=False, server_default="people"),
        sa.Column("likelihood_code", sa.String(4), nullable=False),
        sa.Column("severity_level", sa.SmallInteger, nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        # Snapshots, so a revised matrix leaves signed assessments readable.
        sa.Column("likelihood_label", sa.String(100), nullable=True),
        sa.Column("severity_label", sa.String(300), nullable=True),

        # After controls. Null until someone assesses the residual risk, which
        # is the entire point of recording controls at all.
        sa.Column("residual_likelihood_code", sa.String(4), nullable=True),
        sa.Column("residual_severity_level", sa.SmallInteger, nullable=True),
        sa.Column("residual_rating", sa.String(20), nullable=True),
        sa.Column("controls", sa.Text, nullable=True),

        sa.Column("assessed_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assessed_by_name", sa.String(255), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_risk_assessments_source", "ehs_risk_assessments",
                    ["source_type", "source_id"])

    conn = op.get_bind()
    for i, (code, label, qual, freq, ratings) in enumerate(_LIKELIHOOD):
        conn.execute(sa.text("""
            insert into ehs_risk_likelihood (code, label, qualitative, frequency, sort_order, ratings)
            values (:c, :l, :q, :f, :s, cast(:r as jsonb))
        """), {"c": code, "l": label, "q": qual, "f": freq, "s": i * 10,
               "r": sa.text("").text or __import__("json").dumps(ratings)})
    for level, people, downtime, reputation in _SEVERITY:
        conn.execute(sa.text("""
            insert into ehs_risk_severity (level, people, downtime, reputation)
            values (:lv, :p, :d, :r)
        """), {"lv": level, "p": people, "d": downtime, "r": reputation})


def downgrade() -> None:
    op.drop_table("ehs_risk_assessments")
    op.drop_table("ehs_risk_severity")
    op.drop_table("ehs_risk_likelihood")
