#!/bin/bash
#
# Nightly backup, run from cron on the host.
#
# Calls `flask backup-now` inside the running app container so the backup goes
# through the same code path as the Backups screen: same pg_dump, same zip
# layout, same manifest, same directory. A separate pg_dump here would drift
# from that and produce archives the screen cannot describe.
#
# Output is appended to a log with a timestamp on every line, because the thing
# you need at 3am is not "did it run tonight" but "when did it last succeed".
#
# crontab:  30 2 * * * /var/local/lab-Chemical-analesis/lab_chemical_app/scripts/backup_cron.sh
set -uo pipefail

CONTAINER=lab-chemical-prod
LOG=/var/log/lab-chemical-backup.log

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" >> "$LOG"; }

if ! docker ps --filter "name=^${CONTAINER}$" --filter status=running --format '{{.Names}}' \
     | grep -q "^${CONTAINER}$"; then
  log "FAIL container ${CONTAINER} is not running; no backup taken"
  exit 1
fi

out=$(docker exec "$CONTAINER" flask backup-now 2>&1)
rc=$?

while IFS= read -r line; do
  [ -n "$line" ] && log "$line"
done <<< "$out"

if [ $rc -ne 0 ]; then
  log "FAIL flask backup-now exited $rc"
  exit $rc
fi

log "OK"
