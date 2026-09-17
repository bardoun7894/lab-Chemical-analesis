"""Tests for bi_service — the seven Phase-2 BI panels.

All fixtures are hand-counted; decision compares must be case-insensitive.
"""
from datetime import date, time
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.product import Product
from app.models.production_order import ProductionOrder
from app.services import analytics_service, bi_service


def _args(d=None):
    return MultiDict(d or {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, day, decision="ACCEPT", dn=100, cls="K9",
              actual=100.0, iso=120.0, engineer="Eng1"):
        p = Pipe(
            production_date=day, shift=1, ladle_id="L1", pipe_code=no_code,
            no_code=no_code, arrange_pipe=1, diameter=dn, pipe_class=cls,
            actual_weight=actual, iso_weight=iso,
            final_decision_value=decision, shift_engineer=engineer,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _stage(self, pipe, name, decision=None, defective=False,
               d=date(2026, 3, 1), t=None):
        s = PipeStage(
            pipe_id=pipe.id, stage_name=name, decision=decision,
            has_defect=defective, stage_date=d, stage_time=t,
        )
        db.session.add(s)
        db.session.commit()
        return s


# ---------------------------------------------------------------------------
# T001 — ytd_comparison
# ---------------------------------------------------------------------------

class YtdTestCase(_Base):
    def test_monthly_rows_and_delta(self):
        self._pipe("A1", date(2026, 1, 10), "ACCEPT")
        self._pipe("A2", date(2026, 1, 11), "reject")   # lowercase -> still reject
        self._pipe("B1", date(2025, 1, 10), "ACCEPT")
        data = bi_service.ytd_comparison(2026, 2025)
        jan = data["months"][0]
        self.assertEqual(jan["month"], 1)
        self.assertEqual(jan["year"]["count"], 2)
        self.assertEqual(jan["year"]["reject_pct"], 50.0)
        self.assertEqual(jan["compare"]["count"], 1)
        self.assertEqual(jan["compare"]["reject_pct"], 0.0)
        self.assertEqual(jan["delta_count"], 1)

    def test_zero_decisions_reject_pct_is_none(self):
        self._pipe("A1", date(2026, 2, 10), None)
        data = bi_service.ytd_comparison(2026, 2025)
        feb = data["months"][1]
        self.assertIsNone(feb["year"]["reject_pct"])

    def test_empty_db_safe(self):
        data = bi_service.ytd_comparison(2026, 2025)
        self.assertEqual(len(data["months"]), 12)
        self.assertTrue(all(m["year"]["count"] == 0 for m in data["months"]))


# ---------------------------------------------------------------------------
# T002 — stage_reject_matrix
# ---------------------------------------------------------------------------

class StageRejectMatrixTestCase(_Base):
    def test_percentages_and_none_cells(self):
        p1 = self._pipe("A1", date(2026, 1, 5))
        p2 = self._pipe("A2", date(2026, 1, 6))
        self._stage(p1, "Zinc", "ACCEPT", d=date(2026, 1, 5))
        self._stage(p2, "Zinc", "reject", d=date(2026, 1, 6))  # 50% in Jan
        m = bi_service.stage_reject_matrix(2026, 2025)
        self.assertEqual(m["cells"][("Zinc", 1)]["year"], 50.0)
        # Feb has no Zinc decisions -> None, never 0%
        self.assertIsNone(m["cells"][("Zinc", 2)]["year"])
        self.assertIsNone(m["cells"][("Zinc", 1)]["compare"])
        self.assertIn("Zinc", m["stages"])

    def test_display_string_rejects_classified(self):
        # Review finding: stored stage decisions are display strings —
        # 'تالف' and 'rejected' must count as rejects, not exact 'reject' only.
        p1 = self._pipe("A1", date(2026, 1, 5))
        p2 = self._pipe("A2", date(2026, 1, 6))
        p3 = self._pipe("A3", date(2026, 1, 7))
        self._stage(p1, "Zinc", "تالف", d=date(2026, 1, 5))
        self._stage(p2, "Zinc", "rejected", d=date(2026, 1, 6))
        self._stage(p3, "Zinc", "قبول", d=date(2026, 1, 7))   # accept (Arabic)
        m = bi_service.stage_reject_matrix(2026, 2025)
        self.assertAlmostEqual(m["cells"][("Zinc", 1)]["year"], 66.67, places=1)


# ---------------------------------------------------------------------------
# T003 — period_compare + alerts
# ---------------------------------------------------------------------------

class PeriodCompareTestCase(_Base):
    def _fill(self):
        # Period A (Jan): 4 pipes, 1 reject = 25%
        for i in range(3):
            self._pipe(f"A{i}", date(2026, 1, 10 + i), "ACCEPT")
        self._pipe("A3", date(2026, 1, 13), "REJECT")
        # Period B (Feb): 4 pipes, 2 rejects = 50%
        for i in range(2):
            self._pipe(f"B{i}", date(2026, 2, 10 + i), "ACCEPT")
        self._pipe("B2", date(2026, 2, 12), "REJECT")
        self._pipe("B3", date(2026, 2, 13), "REJECT")

    def test_kpis_and_deltas(self):
        self._fill()
        data = bi_service.period_compare(
            date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 2, 1), date(2026, 2, 28),
            thresholds={},
        )
        kpis = {k["key"]: k for k in data["kpis"]}
        self.assertEqual(kpis["produced"]["a"], 4)
        self.assertEqual(kpis["produced"]["b"], 4)
        self.assertEqual(kpis["produced"]["delta"], 0)
        self.assertAlmostEqual(kpis["reject_pct"]["a"], 25.0, places=3)
        self.assertAlmostEqual(kpis["reject_pct"]["b"], 50.0, places=3)
        self.assertAlmostEqual(kpis["reject_pct"]["delta"], 25.0, places=3)

    def test_alert_fires_above_threshold_not_at_it(self):
        self._fill()
        # reject% rises 25pp. Threshold 25.0 -> NO alert (at, not above).
        data = bi_service.period_compare(
            date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 2, 1), date(2026, 2, 28),
            thresholds={"reject_pct": 25.0},
        )
        self.assertEqual([a for a in data["alerts"] if a["key"] == "reject_pct"], [])
        # Threshold 24.9 -> alert.
        data = bi_service.period_compare(
            date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 2, 1), date(2026, 2, 28),
            thresholds={"reject_pct": 24.9},
        )
        alerts = [a for a in data["alerts"] if a["key"] == "reject_pct"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["direction"], "up")

    def test_empty_period_no_data_state(self):
        data = bi_service.period_compare(
            date(2026, 1, 1), date(2026, 1, 31),
            date(2026, 2, 1), date(2026, 2, 28),
            thresholds={},
        )
        self.assertTrue(data["a_empty"])
        self.assertTrue(data["b_empty"])
        self.assertEqual(data["alerts"], [])


# ---------------------------------------------------------------------------
# T004 — stage_funnel
# ---------------------------------------------------------------------------

class StageFunnelTestCase(_Base):
    def test_counts_and_drops(self):
        p1 = self._pipe("A1", date(2026, 3, 1))
        p2 = self._pipe("A2", date(2026, 3, 1))
        self._stage(p1, "CCM")
        self._stage(p2, "CCM")
        self._stage(p1, "Zinc")
        data = bi_service.stage_funnel(analytics_service.parse_filters(_args()))
        rows = {r["stage"]: r for r in data["rows"]}
        self.assertEqual(rows["CCM"]["count"], 2)
        self.assertEqual(rows["Zinc"]["count"], 1)
        self.assertEqual(rows["Zinc"]["drop_abs"], 1)
        self.assertAlmostEqual(rows["Zinc"]["drop_pct"], 50.0, places=3)
        self.assertEqual(rows["CCM"]["drop_abs"], 0)  # first populated stage

    def test_db_driven_stage_list(self):
        from app.models.stage import ProductionStage
        db.session.add(ProductionStage(
            name="Custom Polish", code=None, sort_order=99, is_active=True,
        ))
        db.session.commit()
        p1 = self._pipe("A1", date(2026, 3, 1))
        self._stage(p1, "Custom Polish")
        data = bi_service.stage_funnel(analytics_service.parse_filters(_args()))
        self.assertIn("Custom Polish", [r["stage"] for r in data["rows"]])


# ---------------------------------------------------------------------------
# T005 — defect_heatmap
# ---------------------------------------------------------------------------

class DefectHeatmapTestCase(_Base):
    def _fill(self):
        p1 = self._pipe("A1", date(2026, 3, 1), dn=100, cls="K9")
        p2 = self._pipe("A2", date(2026, 3, 1), dn=100, cls="K9")
        p3 = self._pipe("A3", date(2026, 3, 1), dn=200, cls="C25")
        self._stage(p1, "Zinc", defective=True)
        self._stage(p2, "Zinc", defective=True)
        self._stage(p3, "Zinc", defective=True)

    def test_count_and_percent_metrics(self):
        self._fill()
        f = analytics_service.parse_filters(_args())
        data = bi_service.defect_heatmap(f, metric="count")
        self.assertEqual(data["cells"][(100, "K9")]["defects"], 2)
        self.assertEqual(data["cells"][(100, "K9")]["pipes"], 2)
        self.assertEqual(data["max"], 2)
        data_pct = bi_service.defect_heatmap(f, metric="pct")
        self.assertAlmostEqual(data_pct["cells"][(100, "K9")]["value"], 100.0, places=3)
        self.assertAlmostEqual(data_pct["cells"][(200, "C25")]["value"], 100.0, places=3)

    def test_zero_pipe_cell_is_none(self):
        self._fill()
        data = bi_service.defect_heatmap(
            analytics_service.parse_filters(_args()), metric="pct"
        )
        # (200, "K9") has no pipes at all -> no-data, not 0%
        self.assertIsNone(data["cells"].get((200, "K9")))


# ---------------------------------------------------------------------------
# T006 — saving_matrix
# ---------------------------------------------------------------------------

class SavingMatrixTestCase(_Base):
    def test_cells_reconcile_with_planned_weight(self):
        self._pipe("A1", date(2026, 3, 1), dn=100, cls="K9", actual=100.0, iso=120.0)
        self._pipe("A2", date(2026, 3, 1), dn=100, cls="K9", actual=90.0, iso=0)  # no weight -> excluded
        data = bi_service.saving_matrix(analytics_service.parse_filters(_args()))
        cell = data["cells"][(100, "K9")]
        self.assertEqual(cell["planned"], 120.0)
        self.assertEqual(cell["actual"], 100.0)
        self.assertEqual(cell["saving"], 20.0)
        self.assertEqual(data["excluded_no_weight"], 1)

    def test_product_fallback_in_matrix(self):
        prod = Product(product_code="PX", weight_kg=150.0)
        db.session.add(prod)
        db.session.flush()
        order = ProductionOrder(
            order_number="POX", target_quantity=5, product_id=prod.id,
            order_date=date(2026, 3, 1),
        )
        db.session.add(order)
        db.session.commit()
        p = self._pipe("A1", date(2026, 3, 1), dn=100, cls="K9", actual=130.0, iso=0)
        p.production_order_id = order.id
        db.session.commit()
        data = bi_service.saving_matrix(analytics_service.parse_filters(_args()))
        self.assertEqual(data["cells"][(100, "K9")]["planned"], 150.0)
        self.assertEqual(data["excluded_no_weight"], 0)


# ---------------------------------------------------------------------------
# T007 — data_quality
# ---------------------------------------------------------------------------

class DataQualityTestCase(_Base):
    def test_fill_rates_and_perfect_score(self):
        self._pipe("A1", date(2026, 3, 1))  # fully filled per _pipe defaults
        self._pipe("A2", date(2026, 3, 1), actual=None, iso=0, engineer=None)
        data = bi_service.data_quality()
        fields = {f["key"]: f for f in data["fields"]}
        self.assertEqual(fields["actual_weight"]["filled"], 1)
        self.assertEqual(fields["actual_weight"]["total"], 2)
        self.assertAlmostEqual(fields["actual_weight"]["pct"], 50.0, places=3)
        self.assertLess(data["score"], 100.0)

    def test_complete_fixture_scores_100(self):
        prod = Product(product_code="PX", weight_kg=150.0)
        db.session.add(prod)
        db.session.flush()
        order = ProductionOrder(
            order_number="POX", target_quantity=5, product_id=prod.id,
            order_date=date(2026, 3, 1),
        )
        db.session.add(order)
        db.session.commit()
        p = self._pipe("A1", date(2026, 3, 1), iso=120.0)
        p.production_order_id = order.id
        p.product_id = prod.id
        db.session.commit()
        self._stage(p, "CCM", d=date(2026, 3, 1), t=time(8, 0))
        # give the stage a machine
        from app.models.chemical import Machine
        m = Machine(machine_code="M1", is_active=True)
        db.session.add(m)
        db.session.flush()
        st = PipeStage.query.filter_by(pipe_id=p.id, stage_name="CCM").one()
        st.machine_id = m.id
        db.session.commit()
        data = bi_service.data_quality()
        self.assertEqual(data["score"], 100.0)
        self.assertEqual(data["gaps"], [])


# ---------------------------------------------------------------------------
# T008 — diagnose
# ---------------------------------------------------------------------------

class DiagnoseTestCase(_Base):
    def test_clean_fixture_scores_100(self):
        data = bi_service.diagnose()
        self.assertEqual(data["score"], 100)
        self.assertEqual(data["problems"], [])

    def test_reject_spike_rule(self):
        # Trailing 3 months: ~5% rejects (2/40). This month: 50% (10/20).
        for m in (1, 2, 3):
            for i in range(20):
                self._pipe(f"T{m}-{i}", date(2026, m, (i % 25) + 1),
                           "REJECT" if i < 1 else "ACCEPT")
        for i in range(20):
            self._pipe(f"C-{i}", date(2026, 4, (i % 25) + 1),
                       "REJECT" if i < 10 else "ACCEPT")
        data = bi_service.diagnose(today=date(2026, 4, 15))
        rules = {p["rule"] for p in data["problems"]}
        self.assertIn("reject_spike", rules)
        spike = [p for p in data["problems"] if p["rule"] == "reject_spike"][0]
        self.assertIn("stage", spike["rca_hint"] or "")

    def test_min_denominator_guard(self):
        # Tiny sample (3 pipes, 2 rejects = 67%) must NOT fire the spike rule.
        for i in range(3):
            self._pipe(f"S-{i}", date(2026, 4, 10), "REJECT" if i < 2 else "ACCEPT")
        data = bi_service.diagnose(today=date(2026, 4, 15))
        self.assertNotIn("reject_spike", {p["rule"] for p in data["problems"]})

    def test_trailing_window_excludes_boundary_day(self):
        # A pipe produced on the 1st of the month belongs to the CURRENT
        # window only — it must not also dilute the trailing baseline.
        # 10 decided pipes on Apr 1 (all reject) + 10 decided in trailing
        # months (all accept). If Apr 1 leaked into trailing, pct_trail > 0.
        for i in range(10):
            self._pipe(f"C-{i}", date(2026, 4, 1), "REJECT")
        for m in (1, 2, 3):
            for i in range(4):
                self._pipe(f"T{m}-{i}", date(2026, m, 10), "ACCEPT")
        data = bi_service.diagnose(today=date(2026, 4, 15))
        spikes = [p for p in data["problems"] if p["rule"] == "reject_spike"]
        self.assertEqual(len(spikes), 1)
        # trailing avg must be exactly 0% (12 accepts, 0 rejects)
        self.assertIn("0.0%", spikes[0]["title"])

    def test_stage_concentration_rule(self):
        for i in range(10):
            p = self._pipe(f"R-{i}", date(2026, 4, 10), "REJECT")
            # 8 of 10 rejects come from Zinc
            self._stage(p, "Zinc" if i < 8 else "Hydrotest", "REJECT",
                        d=date(2026, 4, 10))
        data = bi_service.diagnose(today=date(2026, 4, 15))
        conc = [p for p in data["problems"] if p["rule"] == "stage_concentration"]
        self.assertEqual(len(conc), 1)
        self.assertIn("Zinc", conc[0]["title"])

    def test_undecided_rule(self):
        for i in range(20):
            self._pipe(f"U-{i}", date(2026, 4, 10),
                       None if i < 5 else "ACCEPT")  # 25% undecided
        data = bi_service.diagnose(today=date(2026, 4, 15))
        self.assertIn("undecided_pipes", {p["rule"] for p in data["problems"]})

    def test_stage_time_coverage_rule(self):
        for i in range(10):
            p = self._pipe(f"W-{i}", date(2026, 4, 10))
            self._stage(p, "CCM", t=time(8, 0) if i == 0 else None)  # 10%
        data = bi_service.diagnose(today=date(2026, 4, 15))
        self.assertIn("stage_time_gap", {p["rule"] for p in data["problems"]})


if __name__ == "__main__":
    unittest.main()
