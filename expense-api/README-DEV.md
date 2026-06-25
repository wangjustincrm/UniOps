# expense-api — Developer Guide

## Adding a New Expense Form Type

When adding a new expense form type (e.g., `TRN` for Training Expense), modify these files in order:

### 1. Backend (`expense-api/`)

| File | Change |
|------|--------|
| `app/models/expense.py` | Add type constant to `EXPENSE_TYPES` set |
| `app/schemas/expense.py` | Add type-specific fields to `ExpenseCreate`/`ExpenseResponse` if needed |
| `app/crud/expenses.py` | Add type-specific validation in `_validate_claim()` |
| `alembic/versions/` | Add migration if new DB columns are needed |

### 2. Frontend (`oa/src/`)

| File | Change |
|------|--------|
| `pages/expenses/new/` | Create `{Type}Form.tsx` component |
| `pages/expenses/ExpenseListPage.tsx` | Add type to `CLAIM_TYPES` dropdown |
| `services/expenses.ts` | Add type to `ExpenseType` union |

### 3. Policy Config (if type needs configurable limits)

| File | Change |
|------|--------|
| `app/models/expense_policy.py` | Add config fields |
| `alembic/versions/` | Add migration for new columns |
| Admin panel → Expense Config | New sub-section for the type's policy |

---

## Running Locally

```bash
# Install dependencies
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install -r requirements.txt

# Configure
cp .env.example .env

# Run migrations
alembic upgrade head

# Start
uvicorn app.main:app --reload --port 8006
```

## API Documentation

Available at http://localhost:8006/docs when `DEBUG=true` in `.env`.
