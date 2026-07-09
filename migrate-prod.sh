#!/usr/bin/env bash
# Ordered production migrations for the application server.
#
# finance-api MUST run first: it creates posting_events / posting_lines, which
# expense-api's (and approval-api's runtime code) write into. approval-api has no
# migrations of its own. Each step runs a one-off container of the service image
# (no deps started) using the compose env (DB connection from .env).
#
# Usage (on the application server, after `docker compose ... pull`):
#   ./migrate-prod.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.prod.yml"

# finance-api first (hard ordering constraint); the rest in a safe order.
SERVICES="finance-api epms-api mdm-api identity-api budget-api expense-api vms-api booking-api"

for svc in $SERVICES; do
  echo ">> alembic upgrade head: ${svc}"
  $COMPOSE run --rm --no-deps "${svc}" alembic upgrade head
done

echo "✓ All migrations applied."
