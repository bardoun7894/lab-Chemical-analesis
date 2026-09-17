"""Wall thickness, diameter, lining and TA 1012 symbols in SPC + capability.

Dimensional readings only ever lived in the stage popups. They now chart like
any other characteristic, with one point per reading (21 wall readings on a
pipe are 21 observations of the process) and the pipe as the X̄-R subgroup.

Capability is the careful part: the tolerance band is per DN, so a series
spanning several DNs has no single answer and must decline to compute Cp/Cpk
rather than silently picking one DN's band.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.services import capability_service, spc_service


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.ccm = ProductionStage.name_for_code("ccm")
        self.annealing = ProductionStage.name_for_code("annealing")
        self.coating = ProductionStage.name_for_code("coating")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, code, diameter=300):
        pipe = Pipe(production_date=date(2026, 8, 22), ladle_id=code,
                    pipe_code=f"{code}-P1", no_code=f"{code}N1",
                    arrange_pipe=1, diameter=diameter, pipe_class="K9")
        db.session.add(pipe)
        db.session.commit()
        return pipe


class RegistryTest(_Base):
    def test_dimensional_characteristics_are_chartable(self):
        for key in ("wall_thickness", "outer_diameter", "cement_thickness",
                    "coating_thickness"):
            self.assertIn(key, spc_service.CHARACTERISTICS)
            self.assertTrue(spc_service.is_dimensional(key))

    def test_each_standard_symbol_gets_a_characteristic(self):
        self.assertIn("dim_d1", spc_service.CHARACTERISTICS)
        self.assertIn("dim_S1", spc_service.CHARACTERISTICS)

    def test_dimensional_is_not_mechanical(self):
        self.assertFalse(spc_service.is_mechanical("wall_thickness"))


class SeriesTest(_Base):
    def test_every_wall_reading_is_a_point(self):
        pipe = self._pipe("SP1")
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.ccm,
            dimension_profile={"thickness": {"positions": {
                "1": [7.2, 7.3, 7.1], "2": [7.0, 7.4, 7.2]}}},
        ))
        db.session.commit()
        series = spc_service.build_series("wall_thickness", {})
        self.assertEqual(len(series["points"]), 6)
        self.assertAlmostEqual(min(p["value"] for p in series["points"]), 7.0)

    def test_legacy_flat_profile_also_charts(self):
        pipe = self._pipe("SP2")
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.ccm,
            dimension_profile={"thickness": {"samples": {
                "S1": [7.0] * 21}}},
        ))
        db.session.commit()
        series = spc_service.build_series("wall_thickness", {})
        self.assertEqual(len(series["points"]), 21)

    def test_pipe_is_the_subgroup(self):
        for code in ("SP3", "SP4"):
            pipe = self._pipe(code)
            db.session.add(PipeStage(
                pipe_id=pipe.id, stage_name=self.ccm,
                dimension_profile={"thickness": {"positions": {
                    "1": [7.2, 7.3, 7.1]}}},
            ))
        db.session.commit()
        series = spc_service.build_series("wall_thickness", {})
        self.assertEqual(len({p["subgroup"] for p in series["points"]}), 2)

    def test_lining_layers_chart_from_the_coating_stage(self):
        pipe = self._pipe("SP5")
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.coating,
            thickness_profile={"cement": [3.5, 3.6, 3.7],
                               "coating": [70.0, 70.4]},
        ))
        db.session.commit()
        self.assertEqual(
            len(spc_service.build_series("cement_thickness", {})["points"]), 3)
        self.assertEqual(
            len(spc_service.build_series("coating_thickness", {})["points"]), 2)

    def test_symbol_readings_chart_from_either_stage(self):
        pipe = self._pipe("SP6")
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.annealing,
            dimension_profile={"symbols": {"d1": 326.4}}))
        db.session.commit()
        points = spc_service.build_series("dim_d1", {})["points"]
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0]["value"], 326.4)

    def test_no_data_is_an_empty_series_not_an_error(self):
        self.assertEqual(spc_service.build_series("wall_thickness", {})["points"], [])


class CapabilityLimitsTest(_Base):
    def test_single_dn_resolves_limits_from_the_standard(self):
        self._pipe("CP1", diameter=300)
        limits = capability_service.spec_limits_for("outer_diameter", {})
        self.assertAlmostEqual(limits["lsl"], 322.7)
        self.assertAlmostEqual(limits["usl"], 327.0)
        self.assertIn("DN300", limits["source"])

    def test_mixed_dn_declines_rather_than_picking_one(self):
        self._pipe("CP2", diameter=300)
        self._pipe("CP3", diameter=800)
        self.assertIsNone(capability_service.spec_limits_for("outer_diameter", {}))
        note = capability_service.dimensional_limit_note("outer_diameter", {})
        self.assertIn("300", note)
        self.assertIn("800", note)

    def test_explicit_diameter_filter_resolves_limits(self):
        self._pipe("CP4", diameter=300)
        self._pipe("CP5", diameter=800)
        limits = capability_service.spec_limits_for(
            "outer_diameter", {"diameter": 800})
        self.assertAlmostEqual(limits["lsl"], 837.5)

    def test_symbol_without_a_tolerance_has_no_limits_but_explains_why(self):
        self._pipe("CP6", diameter=300)
        # S1 carries a nominal but no tolerance in the scanned table.
        self.assertIsNone(capability_service.spec_limits_for("wall_thickness", {}))
        note = capability_service.dimensional_limit_note("wall_thickness", {})
        self.assertIn("No tolerance on file", note)

    def test_lining_has_no_borrowed_limits(self):
        self._pipe("CP7", diameter=300)
        self.assertIsNone(capability_service.spec_limits_for("cement_thickness", {}))
        self.assertIsNone(
            capability_service.dimensional_limit_note("cement_thickness", {}))

    def test_non_dimensional_characteristics_are_unaffected(self):
        self.assertIsNone(
            capability_service.dimensional_limit_note("carbon", {}))
        self.assertIsNotNone(capability_service.spec_limits_for("carbon"))

    def test_symbol_mapping(self):
        self.assertEqual(
            capability_service.dimensional_symbol_for("wall_thickness"), "S1")
        self.assertEqual(
            capability_service.dimensional_symbol_for("dim_d2"), "d2")
        self.assertIsNone(
            capability_service.dimensional_symbol_for("dim_nope"))


if __name__ == "__main__":
    unittest.main()
