"""Database + settings backup service.

Produces a single self-contained ``.zip`` per backup holding:

* the database snapshot, consistent either way, named for its format:

  - ``lab_chemical.db``   on SQLite — taken through SQLite's own online-backup
    API, not a file copy. Gunicorn runs four workers against this file;
    ``shutil.copy`` during a write would capture a torn page and yield a
    restore that looks fine until it doesn't.
  - ``lab_chemical.sql``  on Postgres — plain ``pg_dump``, restored with
    ``psql -f``. Requires the ``postgresql-client`` package in the image.
    Plain text on purpose: a custom-format archive records the pg_dump version
    and refuses to load on an older client, which silently left every backup
    unrestorable on the database host itself.
* ``data/*.json``      — element rules, mechanical rules, app settings, KPIs,
  dashboards. These are hand-tuned by the client and live only on the server
  disk; a DB-only backup would silently lose them.
* ``manifest.json``    — what was captured, when, by whom, and the row counts,
  so a restore can be sanity-checked before it is trusted.

Backups are written under ``app/data/backups/`` on purpose: ``app/data`` is the
bind-mounted host directory, so backups survive a container rebuild. A path
inside the image (e.g. ``/app/backups``) would be destroyed by the very event
you most want a backup for.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime

from flask import current_app
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app import db


BACKUP_DIRNAME = "backups"
FILENAME_RE = re.compile(r"^backup-\d{8}-\d{6}(-[\w.-]+)?\.zip$")

# Tables whose row counts go in the manifest — the ones that make a restore
# obviously right or obviously wrong at a glance.
COUNTED_TABLES = (
    "pipes",
    "pipe_stages",
    "chemical_analyses",
    "mechanical_tests",
    "production_orders",
    "users",
)


def _uri():
    return current_app.config.get("SQLALCHEMY_DATABASE_URI", "")


def is_postgres():
    """True when the configured database is Postgres rather than SQLite."""
    return _uri().startswith(("postgresql://", "postgresql+"))


def _db_path():
    """Filesystem path of the SQLite database behind SQLALCHEMY_DATABASE_URI."""
    uri = _uri()
    if not uri.startswith("sqlite:///"):
        raise RuntimeError(f"Not a SQLite database (configured: {uri!r})")
    return uri[len("sqlite:///") :]


def _data_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def backup_dir():
    """Backup directory, created on first use."""
    path = current_app.config.get("BACKUP_DIR") or os.path.join(
        _data_dir(), BACKUP_DIRNAME
    )
    os.makedirs(path, exist_ok=True)
    return path


def _safe_path(filename):
    """Resolve a user-supplied backup filename inside the backup dir.

    Rejects anything that is not one of our own generated names, so a crafted
    ``../../etc/passwd`` can never reach ``send_file`` or ``os.remove``.
    """
    if not FILENAME_RE.match(filename or ""):
        return None
    path = os.path.join(backup_dir(), filename)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(backup_dir()):
        return None
    return path if os.path.isfile(path) else None


def _snapshot_sqlite(src_path, dest_path):
    """Consistent copy of a live SQLite file via the online-backup API."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def _snapshot_postgres(dest_path):
    """Consistent dump of the live Postgres database via ``pg_dump``.

    Plain SQL, deliberately, not the ``-Fc`` custom format.

    The custom format is smaller and supports selective restore, but its archive
    carries a version number: this image ships pg_dump 17 while the server is
    Postgres 16, so every backup taken that way failed to restore on the
    database host with "unsupported version (1.16) in file header". A backup
    that needs a client version you do not have where you are restoring is not
    a backup. Plain SQL is ordinary text and loads with ``psql -f`` on any
    reasonably close version, which is the property that matters at 3am.

    The database here is a few hundred kilobytes and the output is compressed
    into the zip anyway, so the size advantage of the custom format buys
    nothing.

    pg_dump runs in a single transaction snapshot, so the result is consistent
    without taking the app offline.

    The password is passed through ``PGPASSWORD`` in the child environment
    rather than in the URL, so it never appears in the process list.
    """
    url = make_url(_uri())
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password

    cmd = [
        "pg_dump",
        "--format=plain",
        "--no-owner",
        "--no-acl",
        f"--host={url.host or 'localhost'}",
        f"--port={url.port or 5432}",
        f"--username={url.username or 'postgres'}",
        f"--file={dest_path}",
        url.database,
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        # Surface pg_dump's own message; a bare "backup failed" is useless at 3am.
        raise RuntimeError(f"pg_dump failed ({proc.returncode}): {proc.stderr.strip()}")


def _row_counts_sql():
    """Row counts read through SQLAlchemy, so it works on either engine."""
    counts = {}
    for table in COUNTED_TABLES:
        try:
            counts[table] = db.session.execute(
                text(f"SELECT COUNT(*) FROM {table}")  # fixed identifiers, not user input
            ).scalar()
        except Exception:  # noqa: BLE001 - a missing table is reported as unknown
            db.session.rollback()
            counts[table] = None
    return counts


def _row_counts(db_path):
    counts = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in COUNTED_TABLES:
            try:
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"  # fixed identifiers, not user input
                ).fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
    finally:
        conn.close()
    return counts


def create_backup(created_by=None, note=""):
    """Create one backup zip. Returns its :func:`describe` dict."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^\w.-]+", "-", (note or "").strip())[:40].strip("-")
    name = f"backup-{stamp}{('-' + slug) if slug else ''}.zip"
    out_path = os.path.join(backup_dir(), name)
    tmp_db = out_path + ".dbtmp"

    postgres = is_postgres()
    try:
        if postgres:
            # Counts come from the live database rather than the dump, because
            # reading a -Fc archive back would mean restoring it first.
            _snapshot_postgres(tmp_db)
            counts = _row_counts_sql()
        else:
            _snapshot_sqlite(_db_path(), tmp_db)
            counts = _row_counts(tmp_db)

        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "created_by": created_by or "",
            "note": note or "",
            "engine": "postgresql" if postgres else "sqlite",
            "db_bytes": os.path.getsize(tmp_db),
            "row_counts": counts,
            "data_files": [],
        }

        data_dir = _data_dir()
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # The member name states the format, so whoever opens the zip in a
            # year knows whether they need sqlite3 or psql.
            zf.write(tmp_db, "lab_chemical.sql" if postgres else "lab_chemical.db")
            if os.path.isdir(data_dir):
                for fname in sorted(os.listdir(data_dir)):
                    if fname.endswith(".json"):
                        zf.write(os.path.join(data_dir, fname), f"data/{fname}")
                        manifest["data_files"].append(fname)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)

    return describe(name)


def describe(filename):
    """Metadata for one backup: size, timestamp, and its manifest if readable."""
    path = os.path.join(backup_dir(), filename)
    stat = os.stat(path)
    info = {
        "filename": filename,
        "size": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime),
        "manifest": None,
        "corrupt": False,
    }
    try:
        with zipfile.ZipFile(path) as zf:
            # A zip that cannot be read is a backup that would fail a restore —
            # say so on the list rather than at 3am.
            if zf.testzip() is not None:
                info["corrupt"] = True
            with zf.open("manifest.json") as fh:
                info["manifest"] = json.load(fh)
    except Exception:
        info["corrupt"] = True
    return info


def list_backups():
    """Newest first. Never raises — an unreadable directory yields ``[]``."""
    try:
        names = [f for f in os.listdir(backup_dir()) if FILENAME_RE.match(f)]
    except OSError:
        return []
    out = []
    for name in names:
        try:
            out.append(describe(name))
        except OSError:
            continue
    out.sort(key=lambda b: b["created_at"], reverse=True)
    return out


def backup_path(filename):
    """Validated on-disk path for download, or ``None``."""
    return _safe_path(filename)


def delete_backup(filename):
    """Delete one backup. Returns True when a file was actually removed."""
    path = _safe_path(filename)
    if not path:
        return False
    os.remove(path)
    return True


def prune_backups(keep=14):
    """Delete all but the newest ``keep`` backups. Returns the names removed.

    Scheduling a backup and pruning it are the same feature: a nightly job with
    no retention fills the volume and then starts failing at the worst possible
    moment. Deletion goes through delete_backup, so it can only ever remove a
    file matching FILENAME_RE — something this service generated.
    """
    backups = list_backups()  # already newest-first
    doomed = backups[keep:] if keep > 0 else []
    removed = []
    for b in doomed:
        try:
            if delete_backup(b["filename"]):
                removed.append(b["filename"])
        except OSError:
            # One unreadable file must not abort the rest of the prune.
            continue
    return removed


def storage_summary():
    """Total count / bytes used, and free space on the backup volume."""
    backups = list_backups()
    used = sum(b["size"] for b in backups)
    try:
        free = shutil.disk_usage(backup_dir()).free
    except OSError:
        free = None
    return {"count": len(backups), "used": used, "free": free}
