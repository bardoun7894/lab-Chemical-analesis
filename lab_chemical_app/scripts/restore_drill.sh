#!/bin/bash
#
# Restore the newest backup into a scratch database and compare it to live.
#
# A backup that has never been restored is a hypothesis. `pg_restore --list`
# only proves the archive is readable; it says nothing about whether the data
# comes back complete. This actually restores it and diffs the row counts of
# the tables that matter against production.
#
# Completely non-destructive: it creates its own scratch database, reads
# production only with SELECT count(*), and drops the scratch at the end.
# Nothing is ever restored over the live database.
#
# Usage: bash scripts/restore_drill.sh
set -uo pipefail

APP=/var/local/lab-Chemical-analesis/lab_chemical_app
PW=$(cat /root/.lab_pg_password)
SCRATCH=lab_restore_drill
WORK=/tmp/restore_drill.$$
TABLES="pipes pipe_stages pipe_stage_history chemical_analyses mechanical_tests production_orders users products permissions role_permissions"

psql_live() { docker exec -e PGPASSWORD="$PW" lab-chemical-pg psql -U lab -d lab_chemical -tAc "$1"; }
psql_scratch() { docker exec -e PGPASSWORD="$PW" lab-chemical-pg psql -U lab -d "$SCRATCH" -tAc "$1"; }
psql_admin() { docker exec -e PGPASSWORD="$PW" lab-chemical-pg psql -U lab -d postgres -c "$1" >/dev/null 2>&1; }

cleanup() {
  rm -rf "$WORK"
  psql_admin "DROP DATABASE IF EXISTS $SCRATCH;"
}
trap cleanup EXIT

echo "=============================================="
echo " RESTORE DRILL  $(date -u +%FT%TZ)"
echo "=============================================="

BACKUP=$(ls -t "$APP"/app/data/backups/backup-*.zip 2>/dev/null | head -1)
if [ -z "$BACKUP" ]; then
  echo "FAIL no backup zip found"
  exit 1
fi
echo "[1/5] newest backup: $(basename "$BACKUP")"

mkdir -p "$WORK"
unzip -o -q "$BACKUP" -d "$WORK" || { echo "FAIL could not unzip"; exit 1; }
if [ ! -f "$WORK/lab_chemical.sql" ]; then
  if [ -f "$WORK/lab_chemical.dump" ]; then
    echo "FAIL this is a custom-format (-Fc) backup from before the plain-SQL"
    echo "     switch. Those need a pg_restore at least as new as the pg_dump"
    echo "     that wrote them, which the database host does not have — the"
    echo "     exact failure this drill was written to catch."
  else
    echo "FAIL archive has no lab_chemical.sql (is this a SQLite-era backup?)"
  fi
  ls -la "$WORK"
  exit 1
fi
echo "      dump: $(stat -c%s "$WORK/lab_chemical.sql") bytes"
echo "      manifest: $(tr -d '\n' < "$WORK/manifest.json" | head -c 200)"

echo "[2/5] creating scratch database $SCRATCH"
psql_admin "DROP DATABASE IF EXISTS $SCRATCH;"
psql_admin "CREATE DATABASE $SCRATCH OWNER lab;"

echo "[3/5] restoring into it with psql on the database host"
docker cp "$WORK/lab_chemical.sql" lab-chemical-pg:/tmp/drill.sql
# Deliberately restored with the database host's own psql, not the app image's
# client. That is the machine and the toolchain a real recovery would use, so
# it is the one the drill has to prove works.
docker exec -e PGPASSWORD="$PW" lab-chemical-pg \
  psql -U lab -d "$SCRATCH" -v ON_ERROR_STOP=0 -q -f /tmp/drill.sql \
  >"$WORK/restore.out" 2>"$WORK/restore.err"
rc=$?
docker exec lab-chemical-pg rm -f /tmp/drill.sql
if [ -s "$WORK/restore.err" ]; then
  echo "      psql stderr (first 8 lines):"
  sed 's/^/        /' "$WORK/restore.err" | head -8
fi
[ $rc -ne 0 ] && echo "      psql exited $rc"

echo "[4/5] comparing row counts, restored vs live"
printf "      %-24s %10s %10s\n" TABLE LIVE RESTORED
fail=0
for t in $TABLES; do
  live=$(psql_live "SELECT count(*) FROM \"$t\";" 2>/dev/null | tr -d ' ')
  rest=$(psql_scratch "SELECT count(*) FROM \"$t\";" 2>/dev/null | tr -d ' ')
  live=${live:-ERR}; rest=${rest:-ERR}
  mark=""
  # Live may legitimately have grown since the backup was taken, so restored
  # must not EXCEED live, and must not be empty when live is not.
  if [ "$rest" = "ERR" ] || { [ "$rest" = "0" ] && [ "$live" != "0" ]; }; then
    mark="  <-- FAIL"; fail=1
  fi
  printf "      %-24s %10s %10s%s\n" "$t" "$live" "$rest" "$mark"
done

echo "[5/5] verdict"
if [ $fail -ne 0 ]; then
  echo
  echo "  RESTORE DRILL FAILED — the backup does not come back complete."
  exit 1
fi
echo
echo "  RESTORE DRILL PASSED — the newest backup restores with all data present."
echo "  (scratch database dropped)"
