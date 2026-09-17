#!/bin/bash
#
# Cut the production app over from SQLite to PostgreSQL.
#
# The migration is re-run from scratch here rather than reusing an earlier one,
# because production keeps taking writes on SQLite right up to the moment the
# container stops. Any Postgres copy made before that point is already stale.
# So: stop the app first, then snapshot, then migrate, then start on Postgres.
#
# The SQLite file is left exactly where it is and stays mounted. It is the
# rollback: dropping the DATABASE_URL line and restarting brings the app back up
# on the old engine with the data as of the cutover. Nothing here deletes it.
#
# Usage: bash scripts/cutover_to_postgres.sh
set -euo pipefail

APP=/var/local/lab-Chemical-analesis/lab_chemical_app
TS=$(date +%Y%m%d-%H%M%S)
DEST=/var/backups/lab-chemical/cutover-$TS
PW=$(cat /root/.lab_pg_password)
PGURL="postgresql+psycopg2://lab:$PW@lab-chemical-pg:5432/lab_chemical"
NET=lab_chemical_app_lab-network
IMG=lab_chemical_app-lab-chemical

mkdir -p "$DEST"
cd "$APP"

echo "=============================================="
echo " CUTOVER TO POSTGRES  ($TS)"
echo "=============================================="
echo

echo "[1/8] stopping the app (downtime starts)"
docker compose stop lab-chemical
echo "      stopped"

echo "[2/8] final SQLite snapshot, taken with nothing writing"
python3 - "$APP/lab_chemical.db" "$DEST/lab_chemical.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
cp "$DEST/lab_chemical.db" "$DEST/migsrc.db"
chmod 666 "$DEST/migsrc.db"
tar czf "$DEST/app_data.tar.gz" -C "$APP" app/data
echo "      snapshot + app/data at $DEST"

echo "[3/8] repairing dangling foreign keys on the copy (never the original)"
docker run --rm -v "$DEST/migsrc.db":/src/lab_chemical.db \
  -v "$APP/scripts/clean_orphan_fks.py":/app/scripts/clean_orphan_fks.py:ro \
  -e TARGET_SQLITE=/src/lab_chemical.db \
  "$IMG" python /app/scripts/clean_orphan_fks.py --apply 2>/dev/null | tail -4

echo "[4/8] migrating into Postgres"
docker run --rm --network "$NET" \
  -v "$DEST/migsrc.db":/src/lab_chemical.db:ro \
  -v "$APP/scripts/migrate_sqlite_to_postgres.py":/app/scripts/migrate_sqlite_to_postgres.py:ro \
  -e SOURCE_SQLITE=/src/lab_chemical.db \
  -e TARGET_POSTGRES="$PGURL" \
  "$IMG" python /app/scripts/migrate_sqlite_to_postgres.py --wipe | tail -8

echo "[5/8] pointing docker-compose at Postgres"
cp docker-compose.yml "$DEST/docker-compose.yml.before"
python3 - "$PW" <<'PY'
import io, sys
pw = sys.argv[1]
p = "docker-compose.yml"
s = io.open(p, encoding="utf-8").read()
# The depends_on below names the postgres service, so that service has to be
# defined or the whole project is invalid and NOTHING starts — including the
# app that was just stopped. Check with the two-space service indent anchored:
# a bare "  postgres:" also matches the six-space depends_on entry, so the
# naive test reports the service as present when only the reference exists.
if "\n  postgres:\n" not in s:
    svc = """  postgres:
    image: postgres:16-alpine
    container_name: lab-chemical-pg
    restart: unless-stopped
    environment:
      POSTGRES_DB: lab_chemical
      POSTGRES_USER: lab
      POSTGRES_PASSWORD: __PW__
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5434:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U lab -d lab_chemical"]
      interval: 5s
      timeout: 5s
      retries: 20
    networks:
      - lab-network

""".replace("__PW__", pw)
    s = s.replace("services:\n", "services:\n" + svc, 1)
    print("      postgres service was missing, added")
if "\nvolumes:" not in s:
    s = s.replace("networks:\n  lab-network:", "volumes:\n  pgdata:\n\nnetworks:\n  lab-network:", 1)

if "DATABASE_URL" in s:
    io.open(p, "w", encoding="utf-8").write(s)
    print("      DATABASE_URL already set, leaving as is")
else:
    url = f"postgresql+psycopg2://lab:{pw}@lab-chemical-pg:5432/lab_chemical"
    s = s.replace(
        "      - FLASK_ENV=production",
        "      - FLASK_ENV=production\n"
        f"      - DATABASE_URL={url}",
        1,
    )
    # Without this the app wins the race on a host reboot, starts before
    # Postgres accepts connections, and all four workers die on the first
    # query. restart:unless-stopped would eventually recover it, but only
    # after a visible outage.
    if "depends_on" not in s:
        s = s.replace(
            "    container_name: lab-chemical-prod",
            "    container_name: lab-chemical-prod\n"
            "    depends_on:\n"
            "      postgres:\n"
            "        condition: service_healthy",
            1,
        )
    io.open(p, "w", encoding="utf-8").write(s)
    print("      DATABASE_URL + depends_on added to the lab-chemical service")
PY

echo "[6/8] starting on Postgres (downtime ends)"
# Validate before starting. An invalid compose file fails the `up` with the app
# still stopped, which turns a one-minute cutover into an outage.
if ! docker compose config --quiet; then
  echo "      compose is invalid — restoring the previous file and restarting on SQLite"
  cp "$DEST/docker-compose.yml.before" docker-compose.yml
  docker compose up -d lab-chemical
  echo "      rolled back. The app is up on SQLite; fix the compose edit and re-run."
  exit 1
fi
docker compose up -d
for i in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Status}}' lab-chemical-prod 2>/dev/null || echo missing)
  [ "$status" = "running" ] && break
  sleep 2
done
docker ps --filter name=lab-chemical-prod --format "      {{.Names}} | {{.Status}}"

echo "[7/8] confirming the app really is on Postgres"
docker exec lab-chemical-prod python /app/scripts/smoke_routes.py 2>&1 | tail -24

echo "[8/8] write path"
docker exec lab-chemical-prod python /app/scripts/smoke_write.py 2>&1 | tail -6

echo
echo "=============================================="
echo " DONE. Rollback if needed:"
echo "   cp $DEST/docker-compose.yml.before $APP/docker-compose.yml"
echo "   cd $APP && docker compose up -d lab-chemical"
echo " The SQLite file is untouched at $APP/lab_chemical.db"
echo "=============================================="
