"""Import all models so Alembic + SQLAlchemy can register them."""
from app.models.catalog import BudgetL1, BudgetAccount  # noqa: F401
from app.models.factor import (  # noqa: F401
    BudgetAccountFactor, BudgetAccountFactorValue,
    FactorTemplate, FactorTemplateValue,
)
from app.models.plan import BudgetPlan, BudgetPlanLine, BudgetPlanBreakdown  # noqa: F401
from app.models.ledger import BudgetLedger  # noqa: F401
from app.models.settings import BudgetSettings  # noqa: F401
