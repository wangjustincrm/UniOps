#!/usr/bin/env bash
# finance-api test runner (dev machine). Pins the test DB to the LOCAL
# uniops-test_postgres — the repo-root .env points at PRODUCTION and conftest
# drops the public schema, so the DB must never be inherited from there.
set -euo pipefail
DB="${TEST_FINANCE_DB:-finance_test_bankrecon}"
PGPW=$(docker exec uniops-test_postgres printenv POSTGRES_PASSWORD | tr -d '\r\n')
exec docker run --rm --network host -v "$PWD:/app" -w /app \
  -e TEST_PG_HOST=localhost -e TEST_PG_PORT=5432 -e TEST_PG_USER=epms \
  -e TEST_PG_PASSWORD="$PGPW" -e TEST_FINANCE_DB="$DB" \
  -e DATABASE_URL="postgresql+asyncpg://epms:$PGPW@localhost:5432/$DB" \
  -e JWT_SECRET_KEY=test-secret \
  ${FINANCE_TEST_IMAGE:-finance-api-test:bankrecon} python -m pytest "$@"
