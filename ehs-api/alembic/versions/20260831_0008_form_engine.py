"""The form engine — Phase 2's foundation, and its largest single unknown.

Five module groups depend on this: inspections, JSAs, permits, contractor
orientation and equipment checks are all "a template someone fills in". Building
them each their own tables would produce five half-engines, so this comes first
and they become configuration.

Not reused from expense-api's custom forms, and it is worth recording why: that
subsystem has `custom_form_definitions` and no answer storage whatsoever. It
attaches an approval chain to an expense claim; nobody ever fills a form in and
keeps what they said. There is nothing here to inherit.

**Double fidelity** is the design decision that everything else follows from.
A submission stores BOTH:

  * `schema_snapshot` — the entire template as it stood when this was filled in.
    Templates get edited: a question is reworded, an option is removed, a whole
    section is added. Rendering a two-year-old inspection against today's
    template would show answers under questions that were never asked, and
    silently drop answers to questions that no longer exist. The snapshot is
    what makes a submission readable forever.
  * `ehs_form_answers` — one row per answer, typed and indexed. The snapshot is
    unqueryable JSON; these rows are what "how many times did the guard fail
    this quarter" reads. Storing only the snapshot would make every report a
    full-table JSON scan.

Both, always. They answer different questions and neither substitutes.

`fail_on` on a question is what turns an inspection into work: an answer
matching it raises a corrective action against the submission. That is the whole
point of a checklist — not the record, the follow-up.

Question types are the thirteen HSE's own forms use, plus grouping, which their
racking inspection needs (nine groups of checks) and which v0.3 missed because a
group is form structure, not a question type.

Revision ID: 20260831_0008
Revises: 20260831_0007
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260831_0008"
down_revision = "20260831_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Template: the thing an HSE coordinator builds ────────────────────────
    op.create_table(
        "ehs_form_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        # inspection | jsa | permit | orientation | toolbox_talk | audit | other
        sa.Column("category", sa.String(30), nullable=False, server_default="inspection"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    # ── Version: edits never mutate a published version ──────────────────────
    #
    # HSE asked for "template modification history / audit tracking". Versioning
    # gives that for free and solves a harder problem: a submission in progress
    # must not change under the person filling it in.
    op.create_table(
        "ehs_form_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        # [{key, type, label, help, required, options[], fail_on, group, ...}]
        sa.Column("schema", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_by_name", sa.String(255), nullable=True),
        sa.Column("change_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("template_id", "version_no", name="uq_ehs_form_version_no"),
    )
    op.create_index("ix_ehs_form_versions_template", "ehs_form_versions", ["template_id"])

    # ── Submission: one filled-in form ──────────────────────────────────────
    op.create_table(
        "ehs_form_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_no", sa.String(20), nullable=False, unique=True),
        sa.Column("template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_versions.id", ondelete="RESTRICT"), nullable=False),
        # The template exactly as it stood. Without it a two-year-old inspection
        # renders against today's questions.
        sa.Column("schema_snapshot", JSONB, nullable=False),

        # The common dimensions every EHS record carries, so one reporting
        # engine can group submissions alongside incidents (PRD 4.1 ②).
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("shift_code", sa.String(20), nullable=True),

        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_by_name", sa.String(255), nullable=True),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_form_submissions_template", "ehs_form_submissions", ["template_id"])
    op.create_index("ix_ehs_form_submissions_location", "ehs_form_submissions", ["location_id"])
    op.create_index("ix_ehs_form_submissions_status", "ehs_form_submissions", ["status"])

    # ── Answers: one row each, because reports read these and not the JSON ──
    op.create_table(
        "ehs_form_answers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_key", sa.String(80), nullable=False),
        sa.Column("question_label", sa.String(500), nullable=False),
        sa.Column("question_type", sa.String(30), nullable=False),
        # One typed column per shape rather than everything as text: "show me
        # every reading over 40" is a query, not a cast.
        sa.Column("value_text", sa.Text, nullable=True),
        sa.Column("value_number", sa.Numeric(18, 4), nullable=True),
        sa.Column("value_date", sa.Date, nullable=True),
        sa.Column("value_bool", sa.Boolean, nullable=True),
        # Person/location/vocabulary references and multi-selects.
        sa.Column("value_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("value_labels", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # True where the answer matched the question's fail_on.
        sa.Column("is_failure", sa.Boolean, nullable=False, server_default="false"),
        # The corrective action this failure raised, if any.
        sa.Column("action_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("submission_id", "question_key", name="uq_ehs_form_answer_key"),
    )
    op.create_index("ix_ehs_form_answers_submission", "ehs_form_answers", ["submission_id"])
    # The index behind "which checks keep failing".
    op.create_index("ix_ehs_form_answers_failure", "ehs_form_answers",
                    ["question_key", "is_failure"])


def downgrade() -> None:
    op.drop_table("ehs_form_answers")
    op.drop_table("ehs_form_submissions")
    op.drop_table("ehs_form_versions")
    op.drop_table("ehs_form_templates")
