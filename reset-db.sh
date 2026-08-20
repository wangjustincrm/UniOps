#!/usr/bin/env bash
# UniOps — Development Database Reset
# DEV ONLY: destroys all local data and rebuilds from scratch
# Usage:
#   ./reset-db.sh           (drop + recreate + run migrations)
#   ./reset-db.sh --seed    (also run seed script)
#
# WARNING: This will destroy all data in the local 'epms' database.
# Do NOT run in production.

set -e

if [ "${FORCE:-}" != "1" ]; then
  echo "⚠️  DEV ONLY — This will destroy ALL local data in the 'epms' database."
  read -rp "Continue? (y/N) " REPLY
  echo
  [[ "$REPLY" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

COMPOSE="docker compose -f docker-compose.dev.yml"

echo ""
echo "── Step 1: Drop and recreate database ───"
$COMPOSE exec postgres psql -U epms -c "DROP DATABASE IF EXISTS epms;" postgres
$COMPOSE exec postgres psql -U epms -c "CREATE DATABASE epms;" postgres
echo "✅ Database recreated"

echo ""
echo "── Step 2: Run migrations ───────────────"
SERVICES="epms-api mdm-api finance-api file-api mrp-api"
for svc in $SERVICES; do
  echo "   ▶ $svc: alembic upgrade head"
  $COMPOSE run --rm "$svc" alembic upgrade head
  echo "   ✅ $svc migrations complete"
done

if [ "$1" = "--seed" ]; then
  echo ""
  echo "── Step 3: Seed data ────────────────────"
  $COMPOSE run --rm epms-api python -m scripts.seed
  echo "✅ Seed data loaded"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Database reset complete"
[ "$1" = "--seed" ] && echo "   Seed data: loaded" || echo "   Seed data: not loaded (use --seed to load)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
