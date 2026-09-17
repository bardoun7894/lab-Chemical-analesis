"""Ladle melt weight (DrAlaa 2026-08-17): /chemical/add gets a Weight field
under Ladle ID. The weight belongs to the ladle pour itself — stored on
ChemicalAnalysis, independent of product/pipe weights.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.user import User
from app.models.permission import seed_default_permissions


class LadleWeightTestCase(unittest.TestCase):
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
        self.user_id = u.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _base_data(self, **extra):
        data = {
            "test_date": "2026-08-17",
            "furnace_id": str(self.furnace_id),
            "ladle_no": "1",
            "decision": "قبول",
            "carbon": "3.6",
        }
        data.update(extra)
        return data

    def test_add_form_has_weight_input(self):
        resp = self.client.get("/chemical/add")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('name="weight"', body)

    def test_add_saves_weight(self):
        resp = self.client.post(
            "/chemical/add",
            data=self._base_data(weight="1250.5"),
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        ca = ChemicalAnalysis.query.filter_by(ladle_no=1).first()
        self.assertIsNotNone(ca)
        self.assertAlmostEqual(ca.weight, 1250.5)

    def test_add_without_weight_is_ok(self):
        resp = self.client.post(
            "/chemical/add",
            data=self._base_data(),
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        ca = ChemicalAnalysis.query.filter_by(ladle_no=1).first()
        self.assertIsNotNone(ca)
        self.assertIsNone(ca.weight)

    def test_edit_updates_weight(self):
        ca = ChemicalAnalysis(
            test_date=date(2026, 8, 17),
            furnace_id=self.furnace_id,
            ladle_no=1,
            day=17,
            month=8,
            year=2026,
            ladle_id="117082026",
            decision="قبول",
            weight=1000.0,
        )
        db.session.add(ca)
        db.session.commit()

        resp = self.client.post(
            f"/chemical/{ca.id}/edit",
            data={
                "test_date": "2026-08-17",
                "furnace_id": str(self.furnace_id),
                "decision": "قبول",
                "weight": "980.25",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertAlmostEqual(ca.weight, 980.25)

    def test_detail_shows_weight(self):
        ca = ChemicalAnalysis(
            test_date=date(2026, 8, 17),
            furnace_id=self.furnace_id,
            ladle_no=1,
            ladle_id="117082026",
            decision="قبول",
            weight=1234.0,
        )
        db.session.add(ca)
        db.session.commit()

        resp = self.client.get(f"/chemical/{ca.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("1234.0", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
