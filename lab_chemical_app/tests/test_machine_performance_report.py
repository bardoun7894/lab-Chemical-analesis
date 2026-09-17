"""Tests for the Machine Performance report and machine attribution.

Machines are recorded per PipeStage (the CCM/casting stage), NOT on the pipe
itself — Pipe.machine_id is legacy and always NULL. The report, the
"group by machine" mode, and the machine_id filter must all read the
CCM-stage machine or they permanently show "Unknown".
"""
from datetime import date, timedelta
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import Machine
from app.services import analytics_service


def _args(d=None):
    return MultiDict(d or {})


class MachinePerformanceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

        self.m10 = Machine(machine_code="M10", is_active=True)
        self.m11 = Machine(machine_code="M11", is_active=True)
        db.session.add_all([self.m10, self.m11])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, machine=None, final="ACCEPT", stage="CCM",
              prod_date=date(2026, 4, 14)):
        p = Pipe(
            production_date=prod_date,
            shift=1,
            ladle_id="L1",
            pipe_code=no_code,
            no_code=no_code,
            arrange_pipe=1,
            diameter=100,
            pipe_class="K9",
            actual_weight=50.0,
            final_decision_value=final,
        )
        db.session.add(p)
        db.session.flush()
        if machine is not None:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name=stage, decision="ACCEPT",
                machine_id=machine.id,
            ))
        db.session.commit()
        return p

    def test_groups_by_ccm_stage_machine(self):
        self._pipe("N1", machine=self.m10)
        self._pipe("N2", machine=self.m10)
        self._pipe("N3", machine=self.m11, final="REJECT")
        self._pipe("N4")  # no machine recorded -> Unknown

        data = analytics_service.machine_performance(
            analytics_service.parse_filters(_args())
        )
        rows = {r["machine"]: r for r in data["rows"]}

        self.assertIn("M10", rows)
        self.assertIn("M11", rows)
        self.assertEqual(rows["M10"]["total"], 2)
        self.assertEqual(rows["M10"]["accept"], 2)
        self.assertEqual(rows["M11"]["total"], 1)
        self.assertEqual(rows["M11"]["reject"], 1)
        self.assertEqual(rows["Unknown"]["total"], 1)

    def test_group_key_machine_uses_ccm_stage(self):
        p = self._pipe("N1", machine=self.m10)
        self.assertEqual(analytics_service.group_key(p, "machine"), "M10")

        p2 = self._pipe("N2")
        self.assertEqual(analytics_service.group_key(p2, "machine"), "Unknown")

    def test_machine_filter_matches_ccm_stage(self):
        self._pipe("N1", machine=self.m10)
        self._pipe("N2", machine=self.m11)
        self._pipe("N3")  # no machine

        f = analytics_service.parse_filters(_args({"machine_id": str(self.m10.id)}))
        query = analytics_service.apply_pipe_filters(Pipe.query, f)
        self.assertEqual([p.no_code for p in query.all()], ["N1"])

    def test_parse_filters_has_no_default_date_floor(self):
        f = analytics_service.parse_filters(_args())
        self.assertIsNone(f["date_from"])

        # explicit dates still parse
        f2 = analytics_service.parse_filters(_args({"date_from": "2026-01-01"}))
        self.assertEqual(f2["date_from"], date(2026, 1, 1))

    def test_old_pipe_visible_without_explicit_dates(self):
        # A pipe far outside any rolling window must still be counted
        # when the user supplies no date filters.
        self._pipe("N1", machine=self.m10, prod_date=date(2020, 1, 1))
        data = analytics_service.machine_performance(
            analytics_service.parse_filters(_args())
        )
        rows = {r["machine"]: r for r in data["rows"]}
        self.assertEqual(rows["M10"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
