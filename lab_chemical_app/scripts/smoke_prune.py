"""
Check that retention deletes the right files and nothing else.

The dangerous part of a prune is not the deleting, it is what it might delete
by mistake. The backups directory also holds hand-made pre-deploy snapshots
(``lab_chemical_pre_spc_2026-07-27.db`` and friends) and a permissions JSON
backup, none of which match FILENAME_RE. This asserts they all survive.

It only ever runs against generated backups, and it verifies the newest are the
ones kept — an off-by-one that pruned newest-first would still leave the right
count behind and pass a naive check.

Usage:
    DATABASE_URL=... python scripts/smoke_prune.py <keep>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.services import backup_service as bs  # noqa: E402


def main():
    keep = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    app = create_app("production")

    with app.app_context():
        directory = bs.backup_dir()
        before_all = sorted(os.listdir(directory))
        before = bs.list_backups()  # newest-first, generated backups only
        foreign_before = [f for f in before_all if not bs.FILENAME_RE.match(f)]

        print(f"directory      : {directory}")
        print(f"generated zips : {len(before)}")
        print(f"other files    : {len(foreign_before)} (must be untouched)")
        for f in foreign_before:
            print(f"                 {f}")
        print(f"keep           : {keep}")
        print()

        expected_kept = [b["filename"] for b in before[:keep]]
        expected_gone = [b["filename"] for b in before[keep:]]

        removed = bs.prune_backups(keep=keep)
        print(f"removed        : {len(removed)}")

        after_all = sorted(os.listdir(directory))
        after = [b["filename"] for b in bs.list_backups()]
        foreign_after = [f for f in after_all if not bs.FILENAME_RE.match(f)]

        ok = True

        if sorted(removed) != sorted(expected_gone):
            print(f"FAIL: removed {sorted(removed)}, expected {sorted(expected_gone)}")
            ok = False

        # The newest must be what survived, not merely the right number of files.
        if sorted(after) != sorted(expected_kept):
            print(f"FAIL: kept {sorted(after)}, expected {sorted(expected_kept)}")
            ok = False

        if foreign_after != foreign_before:
            print(f"FAIL: non-backup files changed: {foreign_before} -> {foreign_after}")
            ok = False

        if not ok:
            return 1

        print(f"kept           : {after}")
        print()
        print("Retention OK: newest kept, oldest removed, unrelated files untouched")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
