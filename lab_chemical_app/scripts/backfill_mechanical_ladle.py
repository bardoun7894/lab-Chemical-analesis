"""Backfill MechanicalTest.ladle_id and replay the lab-decision cascade.

Tests saved through the UI before the mechanical.py fix landed stored an empty
ladle_id (the form's readonly mirror field). propagate_mechanical_result() bails
out on a falsy ladle_id, so those ladles never cascaded and every pipe stayed
WAITING even though its sample had passed.

This repairs the stored rows, then replays propagation per affected ladle.

Usage (inside the container):
    python scripts/backfill_mechanical_ladle.py [--dry-run]
"""

import sys

from app import create_app, db
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe
from app.services.pipe_decision_service import propagate_mechanical_result

DRY_RUN = "--dry-run" in sys.argv


def resolve_ladle(test):
    """Recover the ladle from the linked pipe, or from the pipe_code suffix."""
    if test.pipe_id:
        pipe = Pipe.query.get(test.pipe_id)
        if pipe and pipe.ladle_id:
            return pipe.ladle_id
    if test.pipe_code:
        pipe = Pipe.query.filter_by(pipe_code=test.pipe_code).first()
        if pipe and pipe.ladle_id:
            return pipe.ladle_id
        # pipe_code is "<no_code>-<arrange>-<ladle>"
        parts = test.pipe_code.rsplit("-", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
    return None


def main():
    app = create_app()
    with app.app_context():
        orphans = [t for t in MechanicalTest.query.all() if not t.ladle_id]
        print(f"tests with no ladle_id: {len(orphans)}")

        repaired = []
        for test in orphans:
            ladle = resolve_ladle(test)
            if not ladle:
                print(f"  test {test.id}: UNRESOLVED (pipe_code={test.pipe_code!r})")
                continue
            print(f"  test {test.id}: {test.pipe_code!r} -> ladle {ladle}")
            if not DRY_RUN:
                test.ladle_id = ladle
            repaired.append(test)

        if DRY_RUN:
            print(f"\n[dry-run] would repair {len(repaired)} tests; no cascade replayed")
            return

        db.session.commit()
        print(f"\nrepaired {len(repaired)} tests")

        ladles = sorted({t.ladle_id for t in repaired})
        print(f"replaying cascade for {len(ladles)} ladles: {', '.join(ladles)}")
        for ladle in ladles:
            tests = (
                MechanicalTest.query.filter_by(ladle_id=ladle, status="ACTIVE")
                .order_by(MechanicalTest.id.asc())
                .all()
            )
            for test in tests:
                if test.decision:
                    propagate_mechanical_result(test)
            states = {}
            for pipe in Pipe.query.filter_by(ladle_id=ladle).all():
                states[pipe.lab_decision] = states.get(pipe.lab_decision, 0) + 1
            print(f"  {ladle}: {states}")


if __name__ == "__main__":
    main()
