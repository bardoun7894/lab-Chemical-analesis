#!/bin/bash
#
# Run the test suite twice — once on Postgres, once on SQLite — and write each
# result to its own log.
#
# The point is the comparison. The suite has always run on in-memory SQLite,
# which ignores foreign keys; production runs Postgres, which does not. Any
# test that only fails on the Postgres side was passing for the wrong reason,
# usually because it builds a row that could not exist in production.
#
# Designed to be launched detached (tmux/nohup): an SSH drop must not take the
# run with it, and the logs are the record.
#
# Usage: bash scripts/run_suite_both_engines.sh
set -uo pipefail

APP=/var/local/lab-Chemical-analesis/lab_chemical_app
IMG=lab_chemical_app-lab-chemical
NET=lab_chemical_app_lab-network
PW=$(cat /root/.lab_pg_password)
PGURL="postgresql+psycopg2://lab:${PW}@lab-chemical-pg:5432/lab_test"

PYTEST_SETUP='rm -f /app/__init__.py; pip install --no-cache-dir -q pytest >/dev/null 2>&1; cd /app'

echo "=== Postgres run starting $(date -u +%FT%TZ) ==="
docker run --rm --network "$NET" \
  -v "$APP/tests":/app/tests:ro \
  -v "$APP/app":/app/app:ro \
  -e TEST_DATABASE_URL="$PGURL" \
  -e DATABASE_URL="$PGURL" \
  "$IMG" sh -c "$PYTEST_SETUP && python -m pytest tests -q --tb=line" \
  > /root/suite_pg.log 2>&1
echo "postgres exit=$? $(date -u +%FT%TZ)"

echo "=== SQLite run starting $(date -u +%FT%TZ) ==="
docker run --rm \
  -v "$APP/tests":/app/tests:ro \
  -v "$APP/app":/app/app:ro \
  "$IMG" sh -c "$PYTEST_SETUP && python -m pytest tests -q --tb=line" \
  > /root/suite_sqlite.log 2>&1
echo "sqlite exit=$? $(date -u +%FT%TZ)"

{
  echo "=========== POSTGRES ==========="
  tail -25 /root/suite_pg.log
  echo
  echo "=========== SQLITE ============="
  tail -25 /root/suite_sqlite.log
} > /root/suite_summary.log 2>&1

echo "DONE — summary at /root/suite_summary.log"
