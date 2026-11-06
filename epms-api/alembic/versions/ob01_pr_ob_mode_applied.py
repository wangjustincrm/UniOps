"""purchase_requests.ob_mode_applied: freeze the over-budget mode at submission

Revision ID: ob01_pr_ob_mode_applied
Revises: tn01_task_notified_at
Create Date: 2026-10-16

approval-api rebuilt every PR's step list from Budget Config's CURRENT
over_budget_mode. When the mode moved fm_only → allow (2026-10-16) the
"Over-Budget — Finance Manager" step vanished from every submitted over-budget
PR: approved ones showed their approvers under the wrong roles (ISS-0006,
PR-20261015-0003) and the ones still waiting on the Finance Manager could no
longer be approved by her. The engine now builds the list from the mode frozen
on the PR at submission (approval-api engine._ob_prefix).

Backfill — every PR that has been submitted (status <> 'draft'), from what its
approval actually walked:
  - not over budget                     → 'none'
  - over budget, a Finance Manager step-0 approval event or approve_pr task
    → 'fm_gm_opm' if a GM/OPM acted at step 1, else 'fm_only'
  - over budget, no Finance Manager trace → 'allow' (walked the plain workflow)
Evidence is taken across all approval runs on purpose: a returned PR resumes
where it was sent back from, so the FM's approval may precede the latest
submit. Drafts stay NULL; the mode is frozen when they are submitted.

Measured on production 2026-10-16 (read-only): 6744 none, 10 none-with-a-stale
FM task (over budget on an earlier run, not on the final one), 101 fm_only,
4 allow (3 imported July records + PR-20261016-0009), 0 fm_gm_opm.
"""
from alembic import op
import sqlalchemy as sa

revision = "ob01_pr_ob_mode_applied"
down_revision = "tn01_task_notified_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_requests",
                  sa.Column("ob_mode_applied", sa.String(20), nullable=True))
    op.execute("""
        UPDATE purchase_requests p SET ob_mode_applied = CASE
            WHEN NOT p.over_budget THEN 'none'
            WHEN EXISTS (SELECT 1 FROM approval_events e
                          WHERE e.document_type = 'pr' AND e.document_id = p.id
                            AND e.step_idx = 0 AND e.actor_role = 'finance_manager')
              OR EXISTS (SELECT 1 FROM tasks t
                          WHERE t.document_type = 'pr' AND t.document_id = p.id
                            AND t.type = 'approve_pr' AND t.assigned_role = 'finance_manager')
            THEN CASE WHEN EXISTS (SELECT 1 FROM approval_events e
                                    WHERE e.document_type = 'pr' AND e.document_id = p.id
                                      AND e.step_idx = 1
                                      AND e.actor_role IN ('gm', 'opm', 'gm_or_opm'))
                      THEN 'fm_gm_opm' ELSE 'fm_only' END
            ELSE 'allow'
        END
        WHERE p.status <> 'draft'
    """)


def downgrade() -> None:
    op.drop_column("purchase_requests", "ob_mode_applied")
