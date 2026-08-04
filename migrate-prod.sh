#!/usr/bin/env bash
# Ordered production migrations for the application server.
#
# finance-api MUST run first: it creates posting_events / posting_lines, which
# expense-api's (and approval-api's runtime code) write into. Each step runs a
# one-off container of the service image (no deps started) using the compose
# env (DB connection from .env).
#
# Usage (on the application server, after `docker compose ... pull`):
#   ./migrate-prod.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.prod.yml"

# finance-api first (hard ordering constraint); the rest in a safe order.
# mdm-api MUST run before epms-api: epms-api's ab_remittance_and_vendor_view
# migration reads business_partners.remittance_email, a column mdm-api adds in
# its own 0006_partner_remittance_email migration (that epms migration guards
# with an explicit RuntimeError if run out of order).
# approval-api runs right after identity-api: its routing tables have no FKs,
# but its migrations start depending on identity's user_roles being seeded.
# mrp-api owns its own alembic_version_mrp table and its first migration has
# down_revision=None (no dependency on any other service's schema) — its
# position in this list is unconstrained, appended here so a release can
# never ship the mrp-api image without its tables (WMS_* inventory-lot mirror)
# while /health still reports healthy.
SERVICES="finance-api mdm-api epms-api identity-api approval-api budget-api expense-api vms-api booking-api mrp-api"

for svc in $SERVICES; do
  echo ">> alembic upgrade head: ${svc}"
  $COMPOSE run --rm --no-deps "${svc}" alembic upgrade head
done

echo "✓ All migrations applied."
