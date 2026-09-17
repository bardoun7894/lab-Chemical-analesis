"""Unit tests for the NonConformanceAction model (cover action on the
Non-Conformance Register)."""
import unittest
from datetime import date

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.nonconformance_action import NonConformanceAction
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage


class NonConformanceActionModelTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_create_and_lookup(self):
        a = NonConformanceAction(
            source="chemical",
            source_code="L42",
            root_cause="Fe above upper limit",
            corrective_action="Add ferro-silicon; re-test.",
            responsible="A. Hassan",
            responsible_date=None,
            status="Open",
        )
        db.session.add(a)
        db.session.commit()

        got = NonConformanceAction.query.filter_by(
            source="chemical", source_code="L42"
        ).first()
        self.assertIsNotNone(got)
        self.assertEqual(got.corrective_action, "Add ferro-silicon; re-test.")
        self.assertEqual(got.status, "Open")
        self.assertIsNotNone(got.created_at)

    def test_load_actions_for_rows(self):
        from app.services import nonconformance_service

        db.session.add(
            NonConformanceAction(
                source="chemical", source_code="L1", status="Done", responsible="x"
            )
        )
        db.session.add(
            NonConformanceAction(
                source="mechanical", source_code="P9", status="Open"
            )
        )
        db.session.commit()

        rows = [
            {"source": "chemical", "source_code": "L1"},
            {"source": "mechanical", "source_code": "P9"},
            {"source": "stage", "source_code": "P12"},  # no action
        ]
        out = nonconformance_service.load_actions(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[("chemical", "L1")].status, "Done")
        self.assertEqual(out[("mechanical", "P9")].to_dict()["responsible"], "")
        self.assertNotIn(("stage", "P12"), out)

    def test_save_action_endpoint_upserts(self):
        from app.models.user import User

        u = User(username="qa", full_name="QA User", role="viewer")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(u.id)
            sess["_fresh"] = True

        # First save: creates a row
        r1 = self.client.post(
            "/reports/non-conformance/action/save",
            json={
                "source": "chemical",
                "source_code": "L7",
                "root_cause": "high Fe",
                "corrective_action": "add FeSi",
                "responsible": "Mona",
                "responsible_date": "2026-08-20",
                "status": "In Progress",
            },
        )
        self.assertEqual(r1.status_code, 200, r1.get_data(as_text=True))
        body = r1.get_json()
        self.assertEqual(body["status"], "In Progress")

        from app.models.nonconformance_action import NonConformanceAction

        a = NonConformanceAction.query.filter_by(
            source="chemical", source_code="L7"
        ).first()
        self.assertIsNotNone(a)
        self.assertEqual(a.corrective_action, "add FeSi")

        # Second save with same key + new status: updates, does not duplicate
        r2 = self.client.post(
            "/reports/non-conformance/action/save",
            json={
                "source": "chemical",
                "source_code": "L7",
                "root_cause": "high Fe",
                "corrective_action": "add FeSi + re-test",
                "responsible": "Mona",
                "responsible_date": "2026-08-20",
                "status": "Done",
            },
        )
        self.assertEqual(r2.status_code, 200)
        all_rows = NonConformanceAction.query.filter_by(
            source="chemical", source_code="L7"
        ).all()
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(all_rows[0].status, "Done")
        self.assertEqual(all_rows[0].corrective_action, "add FeSi + re-test")

    # ---- register filter helpers ---------------------------------------
    def _chem_source(self, ladle_id):
        db.session.add(ChemicalAnalysis(
            test_date=date.today(), ladle_no=1, ladle_id=ladle_id, decision="Hold"
        ))
        db.session.commit()

    def _mech_source(self, pipe_code):
        db.session.add(MechanicalTest(
            test_date=date.today(), pipe_code=pipe_code,
            decision="FAIL", status="ACTIVE",
        ))
        db.session.commit()

    def _stage_source(self, pipe_code):
        pipe = Pipe(
            production_date=date.today(), pipe_code=pipe_code, no_code=pipe_code
        )
        db.session.add(pipe)
        db.session.commit()
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name="Casting",
            has_defect=True, stage_date=date.today(),
        ))
        db.session.commit()

    def _register(self, filters):
        from app.services import nonconformance_service

        with self.app.test_request_context():
            return nonconformance_service.build_register(filters)

    def test_filter_by_cover_status_none(self):
        from app.services import nonconformance_service

        self._chem_source("L_ACT")
        self._mech_source("P_DONE")
        self._stage_source("P_NONE")
        db.session.add(NonConformanceAction(
            source="chemical", source_code="L_ACT", status="Open"
        ))
        db.session.add(NonConformanceAction(
            source="mechanical", source_code="P_DONE", status="Done"
        ))
        db.session.commit()

        filters = nonconformance_service.parse_filters(MultiDict({
            "cover_status": "none",
        }))
        rows_out, _summary, _actions = self._register(filters)
        # 'none' filter strips rows that have any action
        codes = {r["source_code"] for r in rows_out}
        self.assertEqual(codes, {"P_NONE"})

    def test_filter_by_cover_status_done(self):
        from app.services import nonconformance_service

        self._chem_source("L_ACT")
        self._mech_source("P_DONE")
        db.session.add(NonConformanceAction(
            source="chemical", source_code="L_ACT", status="Open"
        ))
        db.session.add(NonConformanceAction(
            source="mechanical", source_code="P_DONE", status="Done"
        ))
        db.session.commit()

        filters = nonconformance_service.parse_filters(MultiDict({"cover_status": "Done"}))
        rows_out, _summary, _actions = self._register(filters)
        codes = {r["source_code"] for r in rows_out}
        self.assertEqual(codes, {"P_DONE"})

    def test_filter_by_responsible_date_range(self):
        from app.services import nonconformance_service

        self._chem_source("L_IN")
        self._mech_source("P_BEFORE")
        self._stage_source("P_NONE")
        db.session.add(NonConformanceAction(
            source="chemical", source_code="L_IN",
            status="Open", responsible_date=date(2026, 8, 15),
        ))
        db.session.add(NonConformanceAction(
            source="mechanical", source_code="P_BEFORE",
            status="Open", responsible_date=date(2026, 7, 1),
        ))
        db.session.add(NonConformanceAction(
            source="stage", source_code="P_NONE",
            status="Open", responsible_date=None,
        ))
        db.session.commit()

        filters = nonconformance_service.parse_filters(MultiDict({
            "responsible_from": "2026-08-01",
            "responsible_to": "2026-08-31",
        }))
        rows_out, _summary, _actions = self._register(filters)
        codes = {r["source_code"] for r in rows_out}
        # L_IN is in range; P_BEFORE is before; P_NONE has no date → excluded
        self.assertEqual(codes, {"L_IN"})

    def test_filter_responsible_from_open_ended(self):
        from app.services import nonconformance_service

        self._chem_source("L_EARLY")
        self._mech_source("P_LATE")
        db.session.add(NonConformanceAction(
            source="chemical", source_code="L_EARLY",
            status="Open", responsible_date=date(2026, 6, 1),
        ))
        db.session.add(NonConformanceAction(
            source="mechanical", source_code="P_LATE",
            status="Open", responsible_date=date(2026, 12, 31),
        ))
        db.session.commit()

        filters = nonconformance_service.parse_filters(MultiDict({
            "responsible_from": "2026-08-01",
        }))
        rows_out, _summary, _actions = self._register(filters)
        codes = {r["source_code"] for r in rows_out}
        # Only P_LATE (>= 2026-08-01)
        self.assertEqual(codes, {"P_LATE"})
