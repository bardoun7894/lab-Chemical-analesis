"""The ladle Batch No. field is gone from the chemical analysis screens.

DrAlaa 2026-09-15: the certificate batch is the Annealing batch
(ANN-YYYYMMDD-N, from when the pipe entered the furnace), not a number typed
per ladle. The ``batch_no`` column stays so no data or migration is touched,
but the form no longer offers it and the routes no longer write it.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.user import User
from app.models.permission import seed_default_permissions


class ChemicalBatchNoRemovedTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        furnace = Furnace(furnace_code="F1", furnace_name="Furnace 1")
        db.session.add(furnace)

        u = User(username="admin", full_name="Admin", role="admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()

        self.furnace_id = furnace.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(u.id)
            sess["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _analysis(self):
        ca = ChemicalAnalysis(
            test_date=date(2026, 9, 5),
            furnace_id=self.furnace_id,
            ladle_no=1,
            day=5,
            month=9,
            year=2026,
            ladle_id="105092026",
            decision="قبول",
            batch_no="LEGACY-BATCH",
        )
        db.session.add(ca)
        db.session.commit()
        return ca

    def test_add_form_has_no_batch_no_input(self):
        resp = self.client.get("/chemical/add")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('name="batch_no"', resp.get_data(as_text=True))

    def test_add_ignores_posted_batch_no(self):
        resp = self.client.post(
            "/chemical/add",
            data={
                "test_date": "2026-09-05",
                "furnace_id": str(self.furnace_id),
                "ladle_no": "1",
                "decision": "قبول",
                "carbon": "3.6",
                "batch_no": "BATCH-001",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        ca = ChemicalAnalysis.query.filter_by(ladle_no=1).first()
        self.assertIsNone(ca.batch_no)

    def test_edit_form_and_detail_do_not_show_batch_no(self):
        ca = self._analysis()
        edit = self.client.get(f"/chemical/{ca.id}/edit").get_data(as_text=True)
        detail = self.client.get(f"/chemical/{ca.id}").get_data(as_text=True)
        self.assertNotIn('name="batch_no"', edit)
        self.assertNotIn("LEGACY-BATCH", edit)
        self.assertNotIn("LEGACY-BATCH", detail)

    def test_edit_leaves_existing_batch_no_untouched(self):
        ca = self._analysis()
        resp = self.client.post(
            f"/chemical/{ca.id}/edit",
            data={
                "test_date": "2026-09-05",
                "furnace_id": str(self.furnace_id),
                "decision": "قبول",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ca.batch_no, "LEGACY-BATCH")


if __name__ == "__main__":
    unittest.main()
