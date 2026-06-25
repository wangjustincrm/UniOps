# budget-api

UniOps Budget microservice (:8007). Owns budget catalogs (L1/L2 accounts), per-CC plans, factor decomposition, and the cross-service ledger used to derive committed/actual spend.

Replaces budget logic previously embedded in epms-api / expense-api / finance-api.

## Local development

```bash
# From project root
docker compose -f docker-compose.dev.yml up budget-api

# Or standalone
cd budget-api
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8007 --reload
```

## Migrations

```bash
alembic upgrade head
alembic revision -m "description" --autogenerate
```

## Docs

- [PRD](./docs/PRD.md)
- [DESIGN](./docs/DESIGN.md)
