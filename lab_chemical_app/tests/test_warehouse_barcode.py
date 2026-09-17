"""The warehouse barcode must survive all three save paths.

This form has three of them — add(), edit_pipe() and the console's
update_stage() — and a field wired into only some is the recurring bug in this
file's history: Delivery fields (2026-07-12), edit-form stage fields
(2026-08-10) and the zinc profile gate (2026-08-29) were each one path short,
and each looked like it worked until someone used the other screen.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.stage import ProductionStage
from app.models.user import User
from app.services.barcode_service import clean_barcode


class WarehouseBarcodeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        admin = User(username="admin", role="admin", is_active=True)
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        self.user_id = admin.id

        self.finish_name = ProductionStage.name_for_code("finish")

        pipe = Pipe(
            production_date=date(2026, 8, 20),
            ladle_id="L1", pipe_code="L1-P1", no_code="N0001",
            arrange_pipe=1, diameter=800, pipe_class="K9",
        )
        db.session.add(pipe)
        db.session.commit()
        self.pipe_id = pipe.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self):
        return db.session.get(Pipe, self.pipe_id)

    def _post_edit(self, **extra):
        data = {
            "no_code": "N0001",
            "production_date": "2026-08-20",
            "ladle_id": "L1",
        }
        data.update(extra)
        return self.client.post(f"/stages/{self.pipe_id}/edit", data=data)

    # --- the rule -----------------------------------------------------------

    def test_digits_only(self):
        self.assertEqual(clean_barcode("2100000172400"), ("2100000172400", None))
        self.assertIsNone(clean_barcode("WH-2026")[0])
        self.assertIsNotNone(clean_barcode("WH-2026")[1])
        self.assertEqual(clean_barcode("  901  "), ("901", None))

    def test_blank_is_allowed(self):
        self.assertEqual(clean_barcode(""), (None, None))
        self.assertEqual(clean_barcode(None), (None, None))

    def test_too_long_is_rejected(self):
        self.assertIsNotNone(clean_barcode("1" * 33)[1])

    # --- the three save paths ----------------------------------------------

    def test_edit_form_saves_the_barcode(self):
        self._login()
        self._post_edit(finish_barcode="9001")
        self.assertEqual(self._pipe().warehouse_barcode, "9001")

    def test_edit_form_can_clear_the_barcode(self):
        """Blanking a mistyped code has to be possible, or the pipe is stuck."""
        self._pipe().warehouse_barcode = "9001"
        db.session.commit()

        self._login()
        self._post_edit(finish_barcode="")
        self.assertIsNone(self._pipe().warehouse_barcode)

    def test_console_saves_the_barcode(self):
        self._login()
        r = self.client.post(
            f"/stages/{self.pipe_id}/stage/{self.finish_name}",
            json={"decision": "Accept", "finish_barcode": "9002"},
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self._pipe().warehouse_barcode, "9002")

    def test_console_leaves_the_barcode_alone_when_not_posted(self):
        """update_stage nulls stage fields a form omits. The barcode is not a
        stage field and must not follow that rule — an unrelated Finish save
        would otherwise wipe it."""
        self._pipe().warehouse_barcode = "9003"
        db.session.commit()

        self._login()
        self.client.post(
            f"/stages/{self.pipe_id}/stage/{self.finish_name}",
            json={"decision": "Accept"},
        )
        self.assertEqual(self._pipe().warehouse_barcode, "9003")

    def test_add_form_saves_the_barcode(self):
        self._login()
        r = self.client.post("/stages/add", data={
            "no_code": "N0002",
            "production_date": "2026-08-21",
            "ladle_id": "L1",
            "arrange_pipe": "2",
            "finish_barcode": "9004",
        })
        self.assertIn(r.status_code, (200, 302))
        created = Pipe.query.filter_by(no_code="N0002").first()
        self.assertIsNotNone(created, "pipe was not created")
        self.assertEqual(created.warehouse_barcode, "9004")

    # --- uniqueness ---------------------------------------------------------

    def test_a_duplicate_is_refused_not_crashed(self):
        other = Pipe(
            production_date=date(2026, 8, 20), ladle_id="L1",
            pipe_code="L1-P2", no_code="N0009", arrange_pipe=2,
            warehouse_barcode="9005",
        )
        db.session.add(other)
        db.session.commit()

        self._login()
        r = self._post_edit(finish_barcode="9005")
        self.assertIn(r.status_code, (200, 302))
        self.assertIsNone(self._pipe().warehouse_barcode)

    def test_resaving_a_pipes_own_barcode_is_not_a_clash(self):
        self._pipe().warehouse_barcode = "9006"
        db.session.commit()

        self._login()
        self._post_edit(finish_barcode="9006")
        self.assertEqual(self._pipe().warehouse_barcode, "9006")

    def test_a_bad_barcode_does_not_save(self):
        self._login()
        self._post_edit(finish_barcode="ABC")
        self.assertIsNone(self._pipe().warehouse_barcode)


if __name__ == "__main__":
    unittest.main()
