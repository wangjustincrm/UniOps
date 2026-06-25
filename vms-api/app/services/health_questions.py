"""Default health questionnaire template + merger with `vms_config`.

PRD §2.2.2 VMS-CI-010 lists the GMP-zone health questions Canada Royal Milk
asks every visitor. We ship them as defaults so VMS works out-of-the-box;
Admin can override (S2-C) via `PUT /admin/health-questions`.

Template shape:
    {
      "version": 1,
      "questions": [
        {"id": "fever_cough", "text": "…", "fail_on": "yes"},
        ...
      ]
    }

`fail_on` is the answer value that makes this question fail the declaration.
Default is "yes" (most safety questions are "are you sick" type — yes = fail).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vms_config import VmsConfig


DEFAULT_HEALTH_QUESTIONS: dict = {
    "version": 1,
    "questions": [
        {
            "id":      "fever_cough",
            "text":    "Have you had fever, cough, or diarrhea in the past 24 hours?",
            "fail_on": "yes",
        },
        {
            "id":      "open_wounds",
            "text":    "Do you have any open wounds, cuts, or skin infections on your hands or arms?",
            "fail_on": "yes",
        },
        {
            "id":      "contact_infectious",
            "text":    "In the past 7 days, have you been in close contact with anyone diagnosed with a communicable disease?",
            "fail_on": "yes",
        },
        {
            "id":      "food_allergens",
            "text":    "Are you carrying food, food allergens (peanuts, etc.), or other items that could contaminate production areas?",
            "fail_on": "yes",
        },
    ],
}


async def get_template(db: AsyncSession) -> dict:
    """Return the active template — admin override from `vms_config` if set,
    falling back to `DEFAULT_HEALTH_QUESTIONS`.

    The merge strategy is "replace, not deep-merge". Once Admin saves their
    own set, the defaults are out — keeps the auditing story clean (the
    template in effect at any moment is exactly one document).
    """
    cfg = (await db.execute(select(VmsConfig).limit(1))).scalar_one_or_none()
    if cfg is None or not cfg.health_questions:
        return DEFAULT_HEALTH_QUESTIONS
    template = cfg.health_questions or {}
    if not template.get("questions"):
        return DEFAULT_HEALTH_QUESTIONS
    return template
