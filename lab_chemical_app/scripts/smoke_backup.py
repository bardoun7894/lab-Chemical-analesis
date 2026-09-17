"""
Create one real backup through backup_service and check it is restorable-looking.

Backups are the thing nobody tests until they need one. This creates an actual
backup on whatever engine DATABASE_URL points at, then reopens the zip and
verifies the snapshot member is present, correctly named for the engine, and
non-trivial in size — a pg_dump that failed silently would otherwise leave a
neat little zip containing nothing.

The backup it creates is real and stays on disk; delete it afterwards if it was
only a test.

Usage:
    DATABASE_URL=... python scripts/smoke_backup.py
"""

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.services import backup_service as bs  # noqa: E402


def main():
    app = create_app("production")
    with app.app_context():
        pg = bs.is_postgres()
        print(f"engine     : {'postgresql' if pg else 'sqlite'}")

        info = bs.create_backup(created_by="smoke-test", note="engine-verify")
        name = info["filename"]
        print(f"file       : {name}")
        print(f"size       : {info['size']:,} bytes")
        print(f"corrupt    : {info['corrupt']}")

        manifest = info["manifest"] or {}
        print(f"engine tag : {manifest.get('engine')}")
        print(f"row counts : {manifest.get('row_counts')}")
        print(f"data files : {len(manifest.get('data_files', []))} json")

        expected = "lab_chemical.sql" if pg else "lab_chemical.db"
        path = bs.backup_path(name)
        if path is None:
            print("FAIL: backup_path refused the name it just generated")
            return 1

        with zipfile.ZipFile(path) as zf:
            members = zf.namelist()
            if expected not in members:
                print(f"FAIL: {expected} missing from zip; has {members}")
                return 1
            size = zf.getinfo(expected).file_size
            print(f"snapshot   : {expected}, {size:,} bytes uncompressed")
            if size < 10_000:
                print(f"FAIL: snapshot is only {size} bytes — the dump likely failed silently")
                return 1

        if info["corrupt"]:
            print("FAIL: backup reports itself corrupt")
            return 1

        print()
        print("Backup OK on this engine")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
