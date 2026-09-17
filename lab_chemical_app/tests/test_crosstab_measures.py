"""Crosstab measures, weight de-duplication, and the two new dimensions.

The crosstab shipped with no tests at all. The weight accumulator was the
reason its weight value was computed but never displayed: build_facts emits one
row per (pipe, stage), and the pipe's weight rode on every one of them, so a
pipe with eight stages contributed its weight eight times. Surfacing that as a
measure without de-duplicating would have printed numbers 8x too large and
looked entirely plausible.
"""

import unittest
from datetime import date, datetime, time

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.product import Product
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import crosstab_service, shift_service


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

        self.user = User(username="qc", full_name="Hossam Reda",
                         role="admin", is_active=True)
        self.user.set_password("x")
        self.product = Product(product_code="P300", weight_kg=100.0)
        db.session.add_all([self.user, self.product])
        db.session.commit()

        self.day = date(2026, 8, 20)
        self.ccm = ProductionStage.name_for_code("ccm")
        self.zinc = ProductionStage.name_for_code("zinc")
        self.cutting = ProductionStage.name_for_code("cutting")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, actual=90.0, diameter=300, with_product=True):
        pipe = Pipe(production_date=self.day, ladle_id="L1", pipe_code=code,
                    no_code=code, arrange_pipe=1, diameter=diameter,
                    pipe_class="K9", iso_weight=0.0, actual_weight=actual,
                    product_id=self.product.id if with_product else None,
                    lab_decision="ACCEPT")
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _stage(self, pipe, name, **kw):
        st = PipeStage(pipe_id=pipe.id, stage_name=name, **kw)
        db.session.add(st)
        db.session.commit()
        return st


class WeightDeduplicationTest(_Base):
    def test_weight_counts_once_per_pipe_not_once_per_stage(self):
        pipe = self._pipe("W1", actual=90.0)
        for name in (self.ccm, self.zinc, self.cutting):
            self._stage(pipe, name, decision="Accept")
        ct = crosstab_service.crosstab({}, [], ["dn"], measures=["weight"])
        self.assertEqual(ct["grand_total"]["weight"], 90.0,
                         "three stages must not triple the pipe's weight")
        self.assertEqual(ct["grand_total"]["count"], 3, "still three fact rows")
        self.assertEqual(ct["grand_total"]["pipes"], 1)

    def test_each_cell_counts_its_own_pipes(self):
        for code in ("W1", "W2"):
            pipe = self._pipe(code, actual=90.0)
            self._stage(pipe, self.ccm, decision="Accept")
            self._stage(pipe, self.zinc, decision="Accept")
        ct = crosstab_service.crosstab({}, ["stage"], ["dn"],
                                       measures=["weight"])
        # Two pipes x 90 kg in every stage column, and in the row total.
        for cell in ct["rows"][0]["cells"]:
            self.assertEqual(cell["weight"], 180.0)
        self.assertEqual(ct["rows"][0]["total"]["weight"], 180.0)

    def test_pipe_with_no_stages_still_contributes_its_weight(self):
        self._pipe("W1", actual=90.0)
        ct = crosstab_service.crosstab({}, [], ["dn"], measures=["weight"])
        self.assertEqual(ct["grand_total"]["weight"], 90.0)


class SavingTest(_Base):
    def test_saving_uses_the_product_standard_weight(self):
        pipe = self._pipe("S1", actual=90.0)
        self._stage(pipe, self.ccm, decision="Accept")
        cell = crosstab_service.crosstab({}, [], ["dn"])["grand_total"]
        self.assertEqual(cell["planned"], 100.0)
        self.assertEqual(cell["weight"], 90.0)
        self.assertEqual(cell["saving_pct"], 10.0)

    def test_saving_is_none_when_nothing_can_be_compared(self):
        pipe = self._pipe("S1", actual=90.0, with_product=False)
        self._stage(pipe, self.ccm, decision="Accept")
        cell = crosstab_service.crosstab({}, [], ["dn"])["grand_total"]
        self.assertIsNone(cell["saving_pct"],
                          "no standard weight means not measurable, not 0%")
        self.assertEqual(cell["weighed"], 0)

    def test_coverage_counts_only_pipes_with_both_weights(self):
        self._pipe("S1", actual=90.0)
        self._pipe("S2", actual=None)
        self._pipe("S3", actual=90.0, with_product=False)
        cell = crosstab_service.crosstab({}, [], ["dn"])["grand_total"]
        self.assertEqual(cell["weighed"], 1)
        self.assertEqual(cell["pipes"], 3)

    def test_negative_saving_is_reported_not_clamped(self):
        pipe = self._pipe("S1", actual=120.0)
        self._stage(pipe, self.ccm, decision="Accept")
        cell = crosstab_service.crosstab({}, [], ["dn"])["grand_total"]
        self.assertLess(cell["saving_pct"], 0)


class MeasureSelectionTest(_Base):
    def test_default_measures(self):
        self._pipe("M1")
        ct = crosstab_service.crosstab({}, [], ["dn"])
        self.assertEqual(ct["measures"], ["acc", "rej", "rej_pct"])
        self.assertFalse(ct["shows_weight"])

    def test_unknown_measures_are_dropped(self):
        self._pipe("M1")
        ct = crosstab_service.crosstab({}, [], ["dn"],
                                       measures=["rej", "nonsense"])
        self.assertEqual(ct["measures"], ["rej"])

    def test_empty_selection_falls_back_to_the_default(self):
        self._pipe("M1")
        self.assertEqual(
            crosstab_service.crosstab({}, [], ["dn"], measures=[])["measures"],
            ["acc", "rej", "rej_pct"])

    def test_weight_measures_flag_coverage_disclosure(self):
        self._pipe("M1")
        ct = crosstab_service.crosstab({}, [], ["dn"], measures=["saving_pct"])
        self.assertTrue(ct["shows_weight"])

    def test_measure_choices_are_localised(self):
        keys = dict(crosstab_service.measure_choices("ar"))
        self.assertEqual(keys["saving_pct"], "التوفير %")
        self.assertEqual(dict(crosstab_service.measure_choices())["rej"],
                         "Rejected")

    def test_result_is_json_serialisable(self):
        """The dashboard payload is serialised; the pipe-id set must not leak."""
        import json
        pipe = self._pipe("M1")
        self._stage(pipe, self.ccm, decision="Accept")
        ct = crosstab_service.crosstab({}, ["stage"], ["dn"])
        json.dumps(ct["rows"] + [ct["grand_total"]] + ct["col_totals"])


class ApproverDimensionTest(_Base):
    def test_approver_resolves_to_a_name(self):
        pipe = self._pipe("A1")
        self._stage(pipe, self.ccm, decision="Accept",
                    approved_by_id=self.user.id)
        ct = crosstab_service.crosstab({}, [], ["approver"])
        self.assertIn(["Hossam Reda"], [r["labels"] for r in ct["rows"]])

    def test_stage_without_an_approver_is_unknown_not_dropped(self):
        pipe = self._pipe("A1")
        self._stage(pipe, self.ccm, decision="Accept")
        ct = crosstab_service.crosstab({}, [], ["approver"])
        self.assertEqual([r["labels"] for r in ct["rows"]],
                         [[crosstab_service.UNKNOWN]])

    def test_approver_is_offered_as_a_dimension(self):
        self.assertIn("approver", crosstab_service.DIMENSIONS)


class PipesAndLengthMeasureTest(_Base):
    """Count and length were computed per cell but not selectable, so the
    "how many pipes, how many metres, per DN and class" question the client
    keeps asking could not be answered on screen at all."""

    def setUp(self):
        super().setUp()
        self.product.length_m = 6.0
        db.session.commit()

    def test_pipes_and_length_are_offered_as_measures(self):
        self.assertIn("pipes", crosstab_service.MEASURES)
        self.assertIn("length", crosstab_service.MEASURES)

    def test_length_counts_once_per_pipe_not_once_per_stage(self):
        pipe = self._pipe("L1")
        for name in (self.ccm, self.zinc, self.cutting):
            self._stage(pipe, name, decision="Accept")
        ct = crosstab_service.crosstab({}, [], ["dn"],
                                       measures=["pipes", "length"])
        self.assertEqual(ct["grand_total"]["pipes"], 1)
        self.assertEqual(ct["grand_total"]["length"], 6.0,
                         "three stages must not triple the pipe's length")

    def test_finish_measurement_beats_the_product_standard(self):
        pipe = self._pipe("L1")
        self._stage(pipe, ProductionStage.name_for_code("finish"),
                    decision="Accept", measurement_type="Length",
                    measurement_value=5.5)
        ct = crosstab_service.crosstab({}, [], ["dn"], measures=["length"])
        self.assertEqual(ct["grand_total"]["length"], 5.5)

    def test_length_has_a_total_in_the_statistics(self):
        for code in ("L1", "L2"):
            pipe = self._pipe(code)
            self._stage(pipe, self.ccm, decision="Accept")
        ct = crosstab_service.crosstab({}, ["stage"], ["dn"],
                                       measures=["length"])
        stat = next(s for s in ct["stats"] if s["measure"] == "length")
        self.assertEqual(stat["total"], 12.0)

    def test_length_is_not_treated_as_a_weight_measure(self):
        self._pipe("L1")
        ct = crosstab_service.crosstab({}, [], ["dn"],
                                       measures=["pipes", "length"])
        self.assertFalse(ct["shows_weight"],
                         "length needs no standard-weight coverage caveat")


class CurrentStageDimensionTest(_Base):
    """`stage` answers "what passed through here"; the client asked "what is
    standing here right now", which needs the pipe's latest decided stage."""

    def test_current_stage_is_the_latest_decided_stage(self):
        pipe = self._pipe("C1")
        self._stage(pipe, self.ccm, decision="Accept")
        self._stage(pipe, self.zinc, decision="Accept")
        ct = crosstab_service.crosstab({}, [], ["current_stage"])
        self.assertEqual([r["labels"] for r in ct["rows"]], [[self.zinc]])

    def test_an_undecided_stage_row_does_not_advance_the_pipe(self):
        pipe = self._pipe("C1")
        self._stage(pipe, self.ccm, decision="Accept")
        self._stage(pipe, self.zinc)          # row exists, no decision yet
        ct = crosstab_service.crosstab({}, [], ["current_stage"])
        self.assertEqual([r["labels"] for r in ct["rows"]], [[self.ccm]])

    def test_a_pipe_with_no_decision_sits_at_the_first_stage(self):
        self._pipe("C1")
        first = ProductionStage.active_names()[0]
        ct = crosstab_service.crosstab({}, [], ["current_stage"])
        self.assertEqual([r["labels"] for r in ct["rows"]], [[first]])

    def test_each_pipe_is_counted_at_exactly_one_current_stage(self):
        for code in ("C1", "C2"):
            pipe = self._pipe(code)
            self._stage(pipe, self.ccm, decision="Accept")
            self._stage(pipe, self.zinc, decision="Accept")
        ct = crosstab_service.crosstab({}, ["current_stage"], ["dn"],
                                       measures=["pipes"])
        totals = {tuple(k): c["pipes"]
                  for k, c in zip(ct["leaf_keys"], ct["col_totals"])}
        self.assertEqual(totals, {(self.zinc,): 2})

    def test_current_stage_and_stage_are_separate_dimensions(self):
        pipe = self._pipe("C1")
        self._stage(pipe, self.ccm, decision="Accept")
        self._stage(pipe, self.zinc, decision="Accept")
        passed = crosstab_service.crosstab({}, [], ["stage"])
        self.assertEqual(len(passed["rows"]), 2, "two stages were traversed")
        standing = crosstab_service.crosstab({}, [], ["current_stage"])
        self.assertEqual(len(standing["rows"]), 1, "it stands at one")

    def test_wip_preset_pivots_on_the_current_stage(self):
        preset = crosstab_service.PRESETS["wip_by_dn"]
        self.assertEqual(preset["pivot"], ["current_stage"])
        self.assertEqual(preset["rows"], ["dn", "pipe_class"])


class StageShiftDimensionTest(_Base):
    def test_stage_time_wins_when_present(self):
        pipe = self._pipe("H1")
        self._stage(pipe, self.ccm, decision="Accept", stage_time=time(9, 30))
        ct = crosstab_service.crosstab({}, [], ["stage_shift"])
        self.assertEqual([r["labels"] for r in ct["rows"]], [["Shift 1"]])

    def test_falls_back_to_the_decision_timestamp(self):
        pipe = self._pipe("H1")
        st = self._stage(pipe, self.ccm, decision="Accept")
        # 20:00 UTC is 21:00 local -> shift 2, not shift 3.
        st.updated_at = datetime(2026, 8, 20, 20, 0)
        db.session.commit()
        ct = crosstab_service.crosstab({}, [], ["stage_shift"])
        self.assertEqual([r["labels"] for r in ct["rows"]], [["Shift 2"]])

    def test_casting_shift_and_decision_shift_are_separate_dimensions(self):
        for key in ("shift", "stage_shift"):
            self.assertIn(key, crosstab_service.DIMENSIONS)


class ShiftHelperTest(unittest.TestCase):
    def test_local_boundaries(self):
        self.assertEqual(shift_service.shift_for_time(time(8, 0)), 1)
        self.assertEqual(shift_service.shift_for_time(time(15, 59)), 1)
        self.assertEqual(shift_service.shift_for_time(time(16, 0)), 2)
        self.assertEqual(shift_service.shift_for_time(time(23, 59)), 2)
        self.assertEqual(shift_service.shift_for_time(time(0, 0)), 3)
        self.assertEqual(shift_service.shift_for_time(time(7, 59)), 3)

    def test_utc_timestamps_are_converted_before_bucketing(self):
        stamp = datetime(2026, 8, 20, 15, 30)   # 16:30 local
        self.assertEqual(shift_service.shift_for_time(stamp), 1,
                         "unconverted, 15:30 reads as shift 1")
        self.assertEqual(shift_service.shift_for_time(stamp, is_utc=True), 2,
                         "converted, 16:30 local is shift 2")

    def test_utc_conversion_can_cross_midnight(self):
        stamp = datetime(2026, 8, 20, 23, 30)   # 00:30 next day, local
        self.assertEqual(shift_service.shift_for_time(stamp, is_utc=True), 3)

    def test_none_is_unknown(self):
        self.assertIsNone(shift_service.shift_for_time(None))
        self.assertIsNone(shift_service.stage_shift(None))


if __name__ == "__main__":
    unittest.main()


class StatisticsTest(_Base):
    def test_statistics_describe_the_visible_cells(self):
        for code, actual in (("T1", 90.0), ("T2", 80.0)):
            pipe = self._pipe(code, actual=actual)
            self._stage(pipe, self.ccm, decision="Accept")
        ct = crosstab_service.crosstab({}, ["stage"], ["dn"],
                                       measures=["weight"])
        stat = next(s for s in ct["stats"] if s["measure"] == "weight")
        self.assertEqual(stat["n"], 1)          # one cell
        self.assertEqual(stat["total"], 170.0)  # both pipes, counted once each

    def test_percentages_get_no_total(self):
        pipe = self._pipe("T1")
        self._stage(pipe, self.ccm, decision="Accept")
        ct = crosstab_service.crosstab({}, ["stage"], ["dn"],
                                       measures=["rej_pct"])
        stat = next(s for s in ct["stats"] if s["measure"] == "rej_pct")
        self.assertIsNone(stat["total"],
                          "summing percentages across cells is meaningless")
        self.assertIsNotNone(stat["avg"])

    def test_statistics_follow_the_row_limit(self):
        for n in range(4):
            pipe = self._pipe(f"T{n}", diameter=100 * (n + 1))
            self._stage(pipe, self.ccm, decision="Accept")
        full = crosstab_service.crosstab({}, ["stage"], ["dn"], row_limit=0)
        cut = crosstab_service.crosstab({}, ["stage"], ["dn"], row_limit=2)
        self.assertGreater(
            next(s for s in full["stats"] if s["measure"] == "acc")["n"],
            next(s for s in cut["stats"] if s["measure"] == "acc")["n"])

    def test_no_data_means_no_statistics_rows(self):
        ct = crosstab_service.crosstab({}, [], ["dn"])
        self.assertEqual(ct["stats"], [])


class ExportAndAiTest(_Base):
    """The Excel button 404'd (no func_map entry) and there was no AI here."""

    def setUp(self):
        super().setUp()
        from app.models.permission import seed_default_permissions
        seed_default_permissions()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user.id)
            sess["_fresh"] = True

    def test_excel_export_no_longer_404s(self):
        pipe = self._pipe("E1")
        self._stage(pipe, self.ccm, decision="Accept")
        self._login()
        resp = self.client.get(
            "/reports/export/bi-dashboard.xlsx?xt_set=1&xt_pivot=stage&xt_rows=dn")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.data), 0)
        self.assertNotIn(b"Unknown report", resp.data)

    def test_export_rows_are_flat_and_scalar(self):
        from app.routes.reports import _crosstab_export
        pipe = self._pipe("E1")
        self._stage(pipe, self.ccm, decision="Accept")
        with self.app.test_request_context("/?xt_set=1&xt_pivot=stage&xt_rows=dn"):
            rows = _crosstab_export({})["export_rows"]
        self.assertTrue(rows)
        for r in rows:
            for k, v in r.items():
                self.assertNotIsInstance(v, (dict, list, set), f"{k} not scalar")

    def test_export_ignores_the_screen_row_limit(self):
        from app.routes.reports import _crosstab_export
        for n in range(4):
            pipe = self._pipe(f"E{n}", diameter=100 * (n + 1))
            self._stage(pipe, self.ccm, decision="Accept")
        with self.app.test_request_context(
                "/?xt_set=1&xt_pivot=stage&xt_rows=dn&xt_limit=2"):
            rows = _crosstab_export({})["export_rows"]
        self.assertEqual(len(rows), 4,
                         "an export must not silently drop rows")

    def test_ai_prompt_carries_the_rendered_table(self):
        from app.services.ai_service import build_report_prompt
        prompt = build_report_prompt("crosstab", {
            "columns": ["Diameter DN", "Stage", "Rejected"],
            "rows": [["DN300", "CCM", 3]],
            "row_dims": ["dn"], "pivot_dims": ["stage"],
            "measures": ["rej"], "shown_rows": 1, "total_rows": 9,
            "coverage": {"shows_weight": True, "weighed": 44, "pipes": 187},
        })
        self.assertIn("DN300", prompt)
        self.assertIn("CCM", prompt)
        self.assertIn("1 من 9", prompt)
        self.assertIn("44 من 187", prompt)

    def test_ai_endpoint_404s_on_an_empty_selection(self):
        self._login()
        resp = self.client.get("/reports/api/ai-summary/crosstab?xt_set=1")
        self.assertEqual(resp.status_code, 404)
