"""Tests for the SPC service: control-limit math vs hand-computed fixtures,
Nelson rule detection, edge cases, and series building from real models.

Fixtures are computed by hand so a regression in the formulas fails loudly.
"""
from datetime import date
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.services import analytics_service
from app.services import spc_service


def _args(d=None):
    return MultiDict(d or {})


# ---------------------------------------------------------------------------
# I-MR limits
# ---------------------------------------------------------------------------

class IMRLimitsTestCase(unittest.TestCase):
    def test_hand_computed_fixture(self):
        # mean = 11.6; MRs = 2,1,2,1 -> mr_bar = 1.5
        # UCL/LCL = mean ± 2.66 * 1.5 = 11.6 ± 3.99
        limits = spc_service.imr_limits([10, 12, 11, 13, 12])
        self.assertAlmostEqual(limits["cl"], 11.6, places=6)
        self.assertAlmostEqual(limits["mr_bar"], 1.5, places=6)
        self.assertAlmostEqual(limits["ucl"], 15.59, places=6)
        self.assertAlmostEqual(limits["lcl"], 7.61, places=6)
        self.assertAlmostEqual(limits["sigma"], 1.5 / 1.128, places=6)
        self.assertFalse(limits["no_variation"])

    def test_fewer_than_two_points_returns_none(self):
        self.assertIsNone(spc_service.imr_limits([]))
        self.assertIsNone(spc_service.imr_limits([5.0]))

    def test_no_variation_state_when_sigma_zero(self):
        limits = spc_service.imr_limits([7.0, 7.0, 7.0, 7.0])
        self.assertEqual(limits["sigma"], 0)
        self.assertTrue(limits["no_variation"])
        # Limits collapse onto CL — never inf/NaN.
        self.assertEqual(limits["ucl"], 7.0)
        self.assertEqual(limits["lcl"], 7.0)


# ---------------------------------------------------------------------------
# X-bar/R limits
# ---------------------------------------------------------------------------

class XbarRLimitsTestCase(unittest.TestCase):
    def test_hand_computed_fixture(self):
        # s1 mean 11.6 range 3 ; s2 mean 10.4 range 3
        # xbar_bar = 11.0, r_bar = 3.0 ; n=5 -> A2=0.577 D3=0 D4=2.114
        limits = spc_service.xbar_r_limits([
            [10, 12, 11, 13, 12],
            [9, 10, 11, 10, 12],
        ])
        self.assertAlmostEqual(limits["cl"], 11.0, places=6)
        self.assertAlmostEqual(limits["r_bar"], 3.0, places=6)
        self.assertAlmostEqual(limits["ucl"], 11.0 + 0.577 * 3.0, places=6)
        self.assertAlmostEqual(limits["lcl"], 11.0 - 0.577 * 3.0, places=6)
        self.assertAlmostEqual(limits["r_ucl"], 2.114 * 3.0, places=6)
        self.assertAlmostEqual(limits["r_lcl"], 0.0, places=6)
        self.assertEqual(limits["subgroup_means"], [11.6, 10.4])

    def test_singleton_subgroups_dropped(self):
        self.assertIsNone(spc_service.xbar_r_limits([[1.0], [2.0]]))
        self.assertIsNone(spc_service.xbar_r_limits([]))


# ---------------------------------------------------------------------------
# P / C chart limits
# ---------------------------------------------------------------------------

class AttributeChartLimitsTestCase(unittest.TestCase):
    def test_p_chart_hand_computed(self):
        # defectives 5,3,2 over n 100,100,50 -> p_bar = 10/250 = 0.04
        limits = spc_service.p_chart_limits(
            [0.05, 0.03, 0.04], [100, 100, 50]
        )
        self.assertAlmostEqual(limits["cl"], 0.04, places=6)
        import math
        ucl100 = 0.04 + 3 * math.sqrt(0.04 * 0.96 / 100)
        self.assertAlmostEqual(limits["limits"][0][0], ucl100, places=6)
        # Lower limit floored at 0
        self.assertEqual(limits["limits"][0][1], 0.0)
        # Smaller subgroup -> wider limits
        ucl50 = 0.04 + 3 * math.sqrt(0.04 * 0.96 / 50)
        self.assertAlmostEqual(limits["limits"][2][0], ucl50, places=6)
        self.assertGreater(ucl50, ucl100)

    def test_c_chart_hand_computed(self):
        import math
        limits = spc_service.c_chart_limits([2, 4, 3, 5, 1])
        self.assertAlmostEqual(limits["cl"], 3.0, places=6)
        self.assertAlmostEqual(limits["ucl"], 3.0 + 3 * math.sqrt(3.0), places=6)
        self.assertEqual(limits["lcl"], 0.0)  # 3 - 3√3 < 0 -> floored

    def test_empty_counts_return_none(self):
        self.assertIsNone(spc_service.c_chart_limits([]))


# ---------------------------------------------------------------------------
# Nelson rules — one synthetic series per rule
# ---------------------------------------------------------------------------

def _rules_fired(values, cl, sigma):
    return {v["rule"] for v in spc_service.nelson_violations(values, cl, sigma)}


class NelsonRulesTestCase(unittest.TestCase):
    def test_rule_1_point_beyond_3sigma(self):
        values = [10.0] * 5 + [16.5] + [10.0] * 5
        violations = spc_service.nelson_violations(values, 10.0, 1.0)
        rule1 = [v for v in violations if v["rule"] == 1]
        self.assertTrue(rule1)
        self.assertEqual(rule1[0]["indices"], [5])

    def test_rule_2_nine_same_side(self):
        values = [10.0, 9.0, 10.0] + [11.0] * 9 + [10.0]
        self.assertIn(2, _rules_fired(values, 10.0, 1.0))

    def test_rule_2_not_fired_by_eight(self):
        values = [10.0] * 3 + [11.0] * 8 + [10.0] * 3
        self.assertNotIn(2, _rules_fired(values, 10.0, 1.0))

    def test_rule_3_six_trending(self):
        values = [10.0] * 5 + [10.2, 10.4, 10.6, 10.8, 11.0, 11.2] + [10.0] * 5
        self.assertIn(3, _rules_fired(values, 10.0, 2.0))

    def test_rule_5_two_of_three_beyond_2sigma(self):
        values = [10.0] * 5 + [12.5, 12.7, 10.0] + [10.0] * 5
        self.assertIn(5, _rules_fired(values, 10.0, 1.0))

    def test_rule_6_four_of_five_beyond_1sigma(self):
        values = [10.0] * 5 + [11.3, 11.4, 11.2, 11.5, 10.0] + [10.0] * 5
        self.assertIn(6, _rules_fired(values, 10.0, 1.0))

    def test_rule_7_fifteen_within_1sigma(self):
        values = [10.5, 9.5] * 8  # 16 points alternating inside ±1σ
        self.assertIn(7, _rules_fired(values, 10.0, 1.0))

    def test_rule_8_eight_beyond_1sigma_both_sides(self):
        values = [10.0] * 4 + [11.5, 8.5] * 4 + [10.0] * 4
        self.assertIn(8, _rules_fired(values, 10.0, 1.0))

    def test_zero_sigma_yields_no_violations(self):
        self.assertEqual(spc_service.nelson_violations([5.0, 5.0, 5.0], 5.0, 0.0), [])

    def test_clean_series_has_no_violations(self):
        values = [10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9, 10.0]
        self.assertEqual(spc_service.nelson_violations(values, 10.0, 1.0), [])


# ---------------------------------------------------------------------------
# build_series over real models
# ---------------------------------------------------------------------------

class BuildSeriesTestCase(unittest.TestCase):
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

    def _mech(self, day, tensile_mpa, status="ACTIVE", ladle="L1", dn=100):
        t = MechanicalTest(
            test_date=day, tensile_mpa=tensile_mpa, status=status,
            ladle_id=ladle, diameter=dn, code=f"T{day.day}",
        )
        db.session.add(t)
        db.session.commit()
        return t

    def test_mechanical_series_active_only_ordered(self):
        self._mech(date(2026, 3, 2), 420.0)
        self._mech(date(2026, 3, 1), 410.0)
        self._mech(date(2026, 3, 3), 999.0, status="SUPERSEDED")

        series = spc_service.build_series(
            "tensile_mpa", analytics_service.parse_filters(_args())
        )
        self.assertEqual([p["value"] for p in series["points"]], [410.0, 420.0])
        self.assertEqual(series["points"][0]["subgroup"], "L1")
        self.assertFalse(series["truncated"])

    def test_mechanical_series_date_filter(self):
        self._mech(date(2026, 1, 15), 400.0)
        self._mech(date(2026, 3, 15), 420.0)
        f = analytics_service.parse_filters(_args({"date_from": "2026-02-01"}))
        series = spc_service.build_series("tensile_mpa", f)
        self.assertEqual(len(series["points"]), 1)
        self.assertEqual(series["points"][0]["value"], 420.0)

    def test_chemical_series_one_point_per_analysis(self):
        for i, day in enumerate([date(2026, 3, 1), date(2026, 3, 2)]):
            db.session.add(ChemicalAnalysis(
                test_date=day, ladle_no=i + 1, ladle_id=f"L{i + 1}",
                carbon=3.5 + i * 0.1,
            ))
        db.session.commit()
        series = spc_service.build_series(
            "carbon", analytics_service.parse_filters(_args())
        )
        self.assertEqual([p["value"] for p in series["points"]], [3.5, 3.6])
        self.assertEqual(series["points"][0]["subgroup"], "L1")

    def test_unknown_characteristic_raises(self):
        with self.assertRaises(ValueError):
            spc_service.build_series(
                "unobtainium", analytics_service.parse_filters(_args())
            )

    def test_series_cap_marks_truncation(self):
        for i in range(5):
            self._mech(date(2026, 3, 1 + i), 400.0 + i)
        series = spc_service.build_series(
            "tensile_mpa", analytics_service.parse_filters(_args()), cap=3
        )
        self.assertEqual(len(series["points"]), 3)
        self.assertEqual(series["total"], 5)
        self.assertTrue(series["truncated"])

    def test_pipe_class_filter_applies_via_pipe_link(self):
        from app.models.pipe import Pipe
        p = Pipe(
            production_date=date(2026, 3, 1), shift=1, ladle_id="L1",
            pipe_code="N1", no_code="N1", arrange_pipe=1, diameter=100,
            pipe_class="K9",
        )
        db.session.add(p)
        db.session.flush()
        linked = MechanicalTest(
            test_date=date(2026, 3, 1), tensile_mpa=420.0, status="ACTIVE",
            ladle_id="L1", code="T1", pipe_id=p.id,
        )
        unlinked = MechanicalTest(
            test_date=date(2026, 3, 2), tensile_mpa=430.0, status="ACTIVE",
            ladle_id="L2", code="T2",
        )
        db.session.add_all([linked, unlinked])
        db.session.commit()

        f = analytics_service.parse_filters(_args({"pipe_class": "K9"}))
        series = spc_service.build_series("tensile_mpa", f)
        self.assertEqual([p["value"] for p in series["points"]], [420.0])

    def test_xbar_limits_disclose_unequal_subgroup_resize(self):
        limits = spc_service.xbar_r_limits([
            [10.0, 12.0],
            [9.0, 11.0, 10.0, 12.0],
        ])
        self.assertTrue(limits["resized"])
        self.assertEqual(limits["subgroup_size"], 2)
        self.assertEqual(limits["original_sizes"], [2, 4])
        limits2 = spc_service.xbar_r_limits([[10.0, 12.0], [9.0, 11.0]])
        self.assertFalse(limits2["resized"])


# ---------------------------------------------------------------------------
# P / C chart data over decisions and defects
# ---------------------------------------------------------------------------

class AttributeDataTestCase(unittest.TestCase):
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

    def _pipe(self, no_code, day, decision, defective_stages=0):
        p = Pipe(
            production_date=day, shift=1, ladle_id="L1", pipe_code=no_code,
            no_code=no_code, arrange_pipe=1, diameter=100,
            final_decision_value=decision,
        )
        db.session.add(p)
        db.session.flush()
        for i in range(defective_stages):
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name=f"Stage{i}", has_defect=True,
                stage_date=day,
            ))
        db.session.commit()
        return p

    def test_p_chart_case_insensitive_decisions(self):
        d = date(2026, 3, 1)
        self._pipe("N1", d, "ACCEPT")
        self._pipe("N2", d, "accept")   # lowercase must still conform
        self._pipe("N3", d, "REJECT")
        self._pipe("N4", d, "HOLD")
        self._pipe("N5", d, None)       # no decision -> excluded from n

        data = spc_service.p_chart_data(
            analytics_service.parse_filters(_args()), group_by="day"
        )
        self.assertEqual(len(data["groups"]), 1)
        g = data["groups"][0]
        self.assertEqual(g["n"], 4)
        self.assertEqual(g["defectives"], 2)
        self.assertEqual(g["hold"], 1)
        self.assertAlmostEqual(g["p"], 0.5, places=6)

    def test_p_chart_groups_by_day(self):
        self._pipe("N1", date(2026, 3, 1), "ACCEPT")
        self._pipe("N2", date(2026, 3, 2), "REJECT")
        data = spc_service.p_chart_data(
            analytics_service.parse_filters(_args()), group_by="day"
        )
        self.assertEqual([g["key"] for g in data["groups"]],
                         ["2026-03-01", "2026-03-02"])

    def test_c_chart_counts_defect_stages_per_day(self):
        self._pipe("N1", date(2026, 3, 1), "ACCEPT", defective_stages=2)
        self._pipe("N2", date(2026, 3, 1), "ACCEPT", defective_stages=1)
        self._pipe("N3", date(2026, 3, 2), "ACCEPT", defective_stages=1)
        data = spc_service.c_chart_data(
            analytics_service.parse_filters(_args()), group_by="day"
        )
        counts = {g["key"]: g["count"] for g in data["groups"]}
        self.assertEqual(counts, {"2026-03-01": 3, "2026-03-02": 1})


if __name__ == "__main__":
    unittest.main()
