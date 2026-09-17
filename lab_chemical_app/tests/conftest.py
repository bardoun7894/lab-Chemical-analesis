"""
Make the suite usable against Postgres.

Every test class calls ``db.drop_all(); db.create_all()`` in setUp. Against
in-memory SQLite that is nearly free. Against Postgres it is 31 real DROPs and
31 real CREATEs per test — with 726 tests that measured out at roughly 2.8
hours, which is not a suite anybody runs.

Rather than rewrite the setUp of 64 test files, this swaps what those two calls
mean when the target is Postgres:

* the schema is built once, on the first call
* every later "drop" is a single ``TRUNCATE ... RESTART IDENTITY CASCADE``
  across all tables, which empties them and resets sequences so tests that
  expect ``id == 1`` still pass
* every later "create" is a no-op, because the schema is already there

On SQLite both calls keep their original behaviour, so the fast local run is
unchanged. Nothing in any test file has to know this happened.
"""

from sqlalchemy import text

from app import db

_original_create_all = db.create_all
_original_drop_all = db.drop_all

# Module-level rather than a fixture: the test classes are unittest.TestCase
# and call db.drop_all() directly from setUp, so the swap has to be in place
# before any of that runs.
_state = {"schema_built": False}


def _is_postgres():
    """True when the bound engine is Postgres. Needs an app context."""
    try:
        return db.engine.url.get_backend_name() == "postgresql"
    except Exception:  # noqa: BLE001 - no engine yet means no Postgres yet
        return False


def _truncate_everything():
    """Empty every table in one statement, resetting sequences.

    CASCADE is required because the tables reference each other; RESTART
    IDENTITY is required because tests assert on specific ids, and a sequence
    that kept climbing between tests would break them in ways that look like
    real failures.
    """
    tables = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
    if not tables:
        return
    db.session.rollback()  # a failed test can leave the session poisoned
    db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.session.commit()


def _fast_drop_all(*args, **kwargs):
    if not _is_postgres():
        return _original_drop_all(*args, **kwargs)

    if not _state["schema_built"]:
        # First call of the session. Really drop, so a database left dirty by
        # an earlier run cannot leak rows into this one.
        return _original_drop_all(*args, **kwargs)

    _truncate_everything()


def _fast_create_all(*args, **kwargs):
    if not _is_postgres():
        return _original_create_all(*args, **kwargs)

    if _state["schema_built"]:
        return
    _original_create_all(*args, **kwargs)
    _state["schema_built"] = True


db.drop_all = _fast_drop_all
db.create_all = _fast_create_all
