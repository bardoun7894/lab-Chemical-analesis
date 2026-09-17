"""Tests for the bi_dashboard aggregate — the in-app replica of DrAlaa's
CCM BI dashboard: same KPI formulas, same groupings, live DB data.
"""
from datetime import date
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import Machine
from app.services import analytics_service, bi_service


def _args(d=None):
    return MultiDict(d or {})


class BiDashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.m10 = Machine(machine_code="M10", is_active=True)
        db.session.add(self.m10)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, day, decision="ACCEPT", dn=100, cls="K9",
              actual=100.0, iso=120.0, shift=1, mold="M1", machine=None,
              defective_reason=None):
        p = Pipe(
            production_date=day, shift=shift, ladle_id="L1", pipe_code=no_code,
            no_code=no_code, arrange_pipe=1, diameter=dn, pipe_class=cls,
            actual_weight=actual, iso_weight=iso, mold_number=mold,
            final_decision_value=decision,
        )
        db.session.add(p)
        db.session.flush()
        if machine is not None:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name="CCM", decision="ACCEPT",
                machine_id=machine.id, stage_date=day,
            ))
        if defective_reason:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name="Zinc", has_defect=True,
                defect_reason=defective_reason, stage_date=day,
            ))
        db.session.commit()
        return p

    def test_kpi_formulas(self):
        # 3 pipes: 2 accept + 1 reject; actual 300, planned 360
        self._pipe("A1", date(2026, 3, 1), "ACCEPT")
        self._pipe("A2", date(2026, 3, 1), "ACCEPT")
        self._pipe("A3", date(2026, 3, 2), "reject")  # lowercase still reject
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        k = data["kpis"]
        self.assertEqual(k["produced"], 3)
        self.assertAlmostEqual(k["actual_kg"], 300.0, places=3)
        self.assertAlmostEqual(k["planned_kg"], 360.0, places=3)
        self.assertAlmostEqual(k["yield_pct"], 300 / 360 * 100, places=2)
        self.assertAlmostEqual(k["reject_pct"], 1 / 3 * 100, places=2)
        self.assertAlmostEqual(k["actual_mt"], 0.3, places=3)
        self.assertAlmostEqual(k["saving_mt"], 0.06, places=3)

    def test_group_aggregations(self):
        self._pipe("A1", date(2026, 3, 1), dn=100, machine=self.m10, shift=1)
        self._pipe("A2", date(2026, 4, 1), dn=200, machine=self.m10, shift=2)
        self._pipe("A3", date(2026, 4, 2), dn=100, shift=1)  # no machine
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        g = data["groups"]
        months = {r["key"]: r for r in g["month"]}
        self.assertEqual(months["2026-03"]["count"], 1)
        self.assertEqual(months["2026-04"]["count"], 2)
        self.assertEqual(months["2026-04"]["weight"], 200.0)
        dns = {r["key"]: r for r in g["dn"]}
        self.assertEqual(dns["DN100"]["count"], 2)
        machines = {r["key"]: r for r in g["machine"]}
        self.assertEqual(machines["M10"]["count"], 2)
        self.assertEqual(machines["Unknown"]["count"], 1)
        shifts = {r["key"]: r for r in g["shift"]}
        self.assertEqual(shifts["Shift 1"]["count"], 2)

    def test_reasons_top10(self):
        for i in range(3):
            self._pipe(f"A{i}", date(2026, 3, 1), defective_reason="Crack")
        self._pipe("B1", date(2026, 3, 1), defective_reason="Dent")
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        reasons = data["reasons"]
        self.assertEqual(reasons[0]["reason"], "Crack")
        self.assertEqual(reasons[0]["count"], 3)
        self.assertEqual(reasons[1]["reason"], "Dent")

    def test_stage_reject_bars(self):
        p1 = self._pipe("A1", date(2026, 3, 1))
        p2 = self._pipe("A2", date(2026, 3, 1))
        db.session.add(PipeStage(pipe_id=p1.id, stage_name="Zinc",
                                 decision="REJECT", stage_date=date(2026, 3, 1)))
        db.session.add(PipeStage(pipe_id=p2.id, stage_name="Zinc",
                                 decision="قبول", stage_date=date(2026, 3, 1)))
        db.session.commit()
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        rej = {r["stage"]: r["rejects"] for r in data["stage"]["reject_by_stage"]}
        self.assertEqual(rej.get("Zinc"), 1)

    def test_daily_trend(self):
        self._pipe("A1", date(2026, 3, 1))
        self._pipe("A2", date(2026, 3, 1))
        self._pipe("A3", date(2026, 3, 2))
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        days = {r["key"]: r for r in data["trends"]["daily"]}
        self.assertEqual(days["2026-03-01"]["count"], 2)
        self.assertEqual(days["2026-03-01"]["weight"], 200.0)
        self.assertEqual(days["2026-03-02"]["count"], 1)

    def test_empty_db_safe(self):
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        self.assertEqual(data["kpis"]["produced"], 0)
        self.assertIsNone(data["kpis"]["yield_pct"])
        self.assertEqual(data["groups"]["month"], [])

    def test_yield_uses_matched_pipe_set(self):
        # Pipe without a planned weight must not inflate the actual side of
        # yield/saving — both sides cover the SAME weighed set.
        self._pipe("A1", date(2026, 3, 1), actual=110.0, iso=120.0)  # weighed
        self._pipe("A2", date(2026, 3, 1), actual=500.0, iso=0)       # no planned
        data = bi_service.bi_dashboard(analytics_service.parse_filters(_args()))
        k = data["kpis"]
        self.assertAlmostEqual(k["yield_pct"], 110 / 120 * 100, places=2)
        self.assertAlmostEqual(k["saving_mt"], 0.01, places=3)
        self.assertAlmostEqual(k["actual_mt"], 0.61, places=3)  # total shown


if __name__ == "__main__":
    unittest.main()
