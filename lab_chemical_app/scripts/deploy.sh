#!/usr/bin/env bash
#
# Deploy git HEAD to production, safely.
#
# The server's git checkout is stale and divergent, so it is never pulled.
# Deployment ships a tar of HEAD and extracts it over the app source, leaving
# the live state alone. Three things on that disk are bind mounts of real
# per-install data and are excluded from the extract:
#
#   lab_chemical.db   the legacy SQLite file
#   app/data/         operator-configured standards, settings, KPIs
#   .env              credentials, including the database password
#
# and one more that is not data but is just as dangerous:
#
#   docker-compose.yml
#
# The compose file used to carry the database password in plain text, so it
# could not be committed; git kept the old SQLite version while the server ran
# Postgres. An extract then replaced the whole stack definition. It is
# parameterised and committed now, but this script still verifies all four are
# byte-identical after the extract and aborts if any moved. An --exclude that
# silently fails to match is exactly how this bit twice.
#
# Usage:  scripts/deploy.sh [--yes]
set -euo pipefail

HOST="${DEPLOY_HOST:-MyContabo}"
DIR="${DEPLOY_DIR:-/var/local/lab-Chemical-analesis/lab_chemical_app}"
APP="${DEPLOY_CONTAINER:-lab-chemical-prod}"
PG="${DEPLOY_PG_CONTAINER:-lab-chemical-pg}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TAR="/tmp/lab-deploy-${STAMP}.tar"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mABORT: %s\033[0m\n' "$*" >&2; exit 1; }

# --- refuse to ship anything that is not committed -------------------------
say "Checking the working tree"
# __pycache__ is tracked in this repo — a historical mistake. The bytecode is
# regenerated inside the container on every build, so a stale .pyc must never
# be the thing that blocks a deploy. Everything else has to be committed,
# because the deploy ships HEAD and not the worktree.
DIRTY="$(git status --porcelain -- app scripts docker-compose.yml Dockerfile requirements.txt \
    | grep -v '__pycache__' || true)"
if [ -n "$DIRTY" ]; then
    printf '%s\n' "$DIRTY"
    die "uncommitted changes — deploy ships git HEAD, so commit or stash first"
fi
HEAD_SHA="$(git rev-parse --short HEAD)"
echo "HEAD is ${HEAD_SHA}: $(git log -1 --pretty=%s)"

if [ "${1:-}" != "--yes" ]; then
    read -r -p "Deploy ${HEAD_SHA} to ${HOST}:${DIR}? [y/N] " reply
    [ "${reply}" = "y" ] || die "cancelled"
fi

# --- back up before touching anything --------------------------------------
say "Backing up the database and the operator settings"
ssh "$HOST" "set -e; cd '$DIR'
    docker exec '$PG' pg_dump -U \"\$(grep -oP '^POSTGRES_USER=\\K.*' .env)\" \
        -d \"\$(grep -oP '^POSTGRES_DB=\\K.*' .env)\" > 'pg-bak-${STAMP}.sql'
    tar -czf 'app-data-bak-${STAMP}.tgz' app/data
    ls -lh 'pg-bak-${STAMP}.sql' 'app-data-bak-${STAMP}.tgz' | sed 's/^/    /'"

# --- fingerprint the untouchables ------------------------------------------
say "Fingerprinting the files the deploy must not move"
BEFORE="$(ssh "$HOST" "cd '$DIR' && md5sum docker-compose.yml .env lab_chemical.db 2>/dev/null; \
    find app/data -type f -exec md5sum {} \; 2>/dev/null | sort -k2")"

# --- ship it ----------------------------------------------------------------
say "Shipping HEAD"
git archive --format=tar -o "$TAR" HEAD
scp -q "$TAR" "${HOST}:${DIR}/deploy.tar"
rm -f "$TAR"

ssh "$HOST" "set -e; cd '$DIR'
    tar -xf deploy.tar \
        --exclude='lab_chemical.db' \
        --exclude='app/data/*' \
        --exclude='.env' \
        --exclude='docker-compose.yml'
    rm -f deploy.tar"

# --- prove nothing live was clobbered ---------------------------------------
say "Verifying the live state is untouched"
AFTER="$(ssh "$HOST" "cd '$DIR' && md5sum docker-compose.yml .env lab_chemical.db 2>/dev/null; \
    find app/data -type f -exec md5sum {} \; 2>/dev/null | sort -k2")"
if [ "$BEFORE" != "$AFTER" ]; then
    printf '%s\n' "$BEFORE" > /tmp/deploy-before.txt
    printf '%s\n' "$AFTER"  > /tmp/deploy-after.txt
    diff /tmp/deploy-before.txt /tmp/deploy-after.txt || true
    die "the extract moved a protected file — restore it before rebuilding"
fi
echo "    all protected files unchanged"

# --- compose must still resolve before we build ------------------------------
say "Validating the compose file against .env"
ssh "$HOST" "cd '$DIR' && docker compose config --quiet" \
    || die "compose does not resolve — check the variables in .env"

# --- build and start ---------------------------------------------------------
say "Building and restarting"
ssh "$HOST" "cd '$DIR' && docker compose build && docker compose up -d"

say "Waiting for the app to answer"
ssh "$HOST" "for i in \$(seq 1 40); do
        code=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9999/login || true)
        [ \"\$code\" = '200' ] && { echo \"    login HTTP 200 after \${i} tries\"; exit 0; }
        sleep 3
    done
    echo '    never answered 200'; docker logs --tail 40 '$APP'; exit 1" \
    || die "the app did not come up — roll back with: docker compose up -d --force-recreate"

# --- report ------------------------------------------------------------------
say "Post-deploy state"
ssh "$HOST" "cd '$DIR'
    docker ps --filter name=lab-chemical --format '    {{.Names}}  {{.Status}}'
    docker exec '$PG' psql -U \"\$(grep -oP '^POSTGRES_USER=\\K.*' .env)\" \
        -d \"\$(grep -oP '^POSTGRES_DB=\\K.*' .env)\" -t \
        -c \"select (select count(*) from pipes)||' pipes, '||(select count(*) from production_orders)||' orders, '||(select count(*) from chemical_analyses)||' analyses, '||(select count(*) from pipe_stages)||' stages'\" | sed 's/^/   /'"

say "Deployed ${HEAD_SHA}. Backups: pg-bak-${STAMP}.sql, app-data-bak-${STAMP}.tgz"
