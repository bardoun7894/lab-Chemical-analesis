"""Pipe Order duplicate handling on registration.

The order field is pre-filled by the form (next available for the ladle), so a
collision there is the app's own race — a stale form, two tabs, or an OCR
autofill that never refreshed the number. Those get bumped silently.

A number the OPERATOR typed is different: they meant #3, they got #7, and they
need to be told. That case keeps the warning.

The bump itself (max+1) and the rebuilt pipe_code happen either way.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.user import User


class PipeOrderAutoBumpTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        db.session.add(
            ChemicalAnalysis(
                test_date=date(2026, 8, 22), ladle_no=1,
                ladle_id="L9", decision="فحص أخيرة فقط",
            )
        )
        db.session.add(
            Pipe(
                production_date=date(2026, 8, 22), ladle_id="L9",
                no_code="P0001", pipe_code="P0001-1-L9", arrange_pipe=1,
                diameter=300, pipe_class="K9",
            )
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _add(self, no_code, **fields):
        data = {
            "no_code": no_code,
            "production_date": "2026-08-22",
            "shift": "1",
            "ladle_id": "L9",
            "arrange_pipe": "1",
            "diameter": "300",
            "pipe_class": "K9",
        }
        data.update(fields)
        return self.client.post("/stages/add", data=data, follow_redirects=True)

    def _created(self, no_code):
        return Pipe.query.filter_by(no_code=no_code).one()

    def test_auto_order_collision_is_bumped_without_warning(self):
        self._login()
        html = self._add("P0020", arrange_pipe_auto="1").get_data(as_text=True)

        pipe = self._created("P0020")
        self.assertEqual(pipe.arrange_pipe, 2)
        self.assertEqual(pipe.pipe_code, "P0020-2-L9")
        self.assertNotIn("already taken", html)

    def test_manually_typed_order_collision_still_warns(self):
        self._login()
        html = self._add("P0021", arrange_pipe_auto="0").get_data(as_text=True)

        pipe = self._created("P0021")
        self.assertEqual(pipe.arrange_pipe, 2)
        self.assertIn("already taken", html)

    def test_free_order_is_never_touched(self):
        self._login()
        html = self._add("P0022", arrange_pipe="5", arrange_pipe_auto="1").get_data(
            as_text=True
        )

        pipe = self._created("P0022")
        self.assertEqual(pipe.arrange_pipe, 5)
        self.assertNotIn("already taken", html)

    def test_edit_keeps_the_warning_for_a_typed_order(self):
        self._login()
        self._add("P0023", arrange_pipe="3", arrange_pipe_auto="1")
        pipe = self._created("P0023")

        html = self.client.post(
            f"/stages/{pipe.id}/edit",
            data={
                "no_code": "P0023",
                "production_date": "2026-08-22",
                "shift": "1",
                "ladle_id": "L9",
                "arrange_pipe": "1",
                "arrange_pipe_auto": "0",
                "diameter": "300",
                "pipe_class": "K9",
                "pipe_code": "P0023-1-L9",
            },
            follow_redirects=True,
        ).get_data(as_text=True)

        db.session.refresh(pipe)
        self.assertNotEqual(pipe.arrange_pipe, 1)
        self.assertIn("already taken", html)


if __name__ == "__main__":
    unittest.main()
