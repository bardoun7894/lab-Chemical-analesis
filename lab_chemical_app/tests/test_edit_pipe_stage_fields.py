"""edit_pipe must persist every stage field the form renders.

Bug 2026-07-26: the inline stage table on /stages/<id>/edit renders Delivery
date/customer/receipt/sales order, Finish bundle/length, and Annealing
date/time — but edit_pipe only read decision/machine/reason/defect/notes,
so all of those were silently dropped on Save.

Annealing: the temperature input is replaced by a Batch Number field (stored
on PipeStage.bundle_number, same column Finish/Delivery use).
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User


class EditPipeStageFieldsTestCase(unittest.TestCase):
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

        # pipes.ladle_id is a foreign key to chemical_analyses.ladle_id, so the
        # ladle has to exist before the pipe. SQLite never enforced it.
        db.session.add(
            ChemicalAnalysis(test_date=date(2026, 4, 14), ladle_no=1, ladle_id="G1")
        )
        db.session.commit()

        self.pipe = Pipe(
            production_date=date(2026, 4, 14),
            ladle_id="G1",
            pipe_code="G1-P1",
            no_code="G0001",
            arrange_pipe=1,
            diameter=300,
            pipe_class="K9",
        )
        db.session.add(self.pipe)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _post_edit(self, **fields):
        base = {
            "no_code": "G0001",
            "production_date": "2026-04-14",
            "shift": "1",
            "diameter": "300",
            "pipe_class": "K9",
            "arrange_pipe": "1",
        }
        base.update(fields)
        return self.client.post(f"/stages/{self.pipe.id}/edit", data=base)

    def _stage(self, name):
        return PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=name
        ).first()

    # --- Delivery --------------------------------------------------------

    def test_edit_saves_delivery_fields(self):
        self._login()
        resp = self._post_edit(
            stage_Delivery_decision="Accept",
            stage_Delivery_date="2026-07-26",
            stage_Delivery_customer="Alex",
            stage_Delivery_receipt="15",
            stage_Delivery_sales_order="SO-1",
        )
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True))

        stage = self._stage("Delivery")
        self.assertIsNotNone(stage, "Delivery stage row was never created")
        self.assertEqual(stage.decision, "Accept")
        self.assertEqual(stage.delivery_date, date(2026, 7, 26))
        self.assertEqual(stage.delivery_customer, "Alex")
        self.assertEqual(stage.delivery_receipt, "15")
        self.assertEqual(stage.sales_order, "SO-1")

    def test_edit_delivery_row_created_from_date_only(self):
        """A delivery date with no decision must still create the stage row —
        the any-data guard used to ignore every delivery field."""
        self._login()
        self._post_edit(stage_Delivery_date="2026-07-26")
        stage = self._stage("Delivery")
        self.assertIsNotNone(stage)
        self.assertEqual(stage.delivery_date, date(2026, 7, 26))

    # --- Finish -----------------------------------------------------------

    def test_edit_saves_finish_bundle_and_length(self):
        self._login()
        self._post_edit(
            stage_Finish_decision="Accept",
            stage_Finish_bundle_number="1200",
            stage_Finish_length="6.0",
        )
        stage = self._stage("Finish")
        self.assertIsNotNone(stage)
        self.assertEqual(stage.bundle_number, "1200")
        self.assertEqual(stage.measurement_value, 6.0)

    # --- Annealing --------------------------------------------------------

    def test_edit_annealing_row_created_and_stamped(self):
        """Posted batch/date/time are ignored — the server stamps its own
        (see test_annealing_auto_batch.py for the full semantics)."""
        self._login()
        self._post_edit(
            stage_Annealing_decision="Accept",
            stage_Annealing_batch_number="B-77",
            stage_Annealing_date="2026-07-22",
            stage_Annealing_time="14:30",
        )
        stage = self._stage("Annealing")
        self.assertIsNotNone(stage)
        self.assertTrue(stage.bundle_number.startswith("ANN-"))
        self.assertIsNotNone(stage.stage_date)
        self.assertIsNotNone(stage.stage_time)

    def test_annealing_form_renders_batch_not_temperature(self):
        self._login()
        html = self.client.get(f"/stages/{self.pipe.id}/edit").get_data(as_text=True)
        self.assertIn("stage_Annealing_batch_number", html)
        self.assertNotIn("stage_Annealing_temperature", html)

    # --- update_stage (console/modal path) --------------------------------

    def test_update_stage_annealing_creates_row(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/Annealing",
            json={"decision": "Accept"},
            headers={"Content-Type": "application/json"},
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        stage = self._stage("Annealing")
        self.assertIsNotNone(stage)
        self.assertTrue(stage.bundle_number.startswith("ANN-"))

    def test_update_stage_annealing_resave_preserves_batch(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/Annealing",
            json={"decision": "Accept"},
            headers={"Content-Type": "application/json"},
        )
        batch = self._stage("Annealing").bundle_number
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/Annealing",
            json={"notes": "checked"},
            headers={"Content-Type": "application/json"},
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        stage = self._stage("Annealing")
        self.assertEqual(stage.bundle_number, batch)
        self.assertEqual(stage.notes, "checked")


if __name__ == "__main__":
    unittest.main()
