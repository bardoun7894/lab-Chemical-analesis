"""Tests for the capability service: Cp/Cpk/Pp/Ppk vs textbook fixtures,
one-sided tensile, low-confidence flag, and spec-limit delegation.

Hand-computed fixture: values [9, 10, 11] x 10 (n=30), LSL=7, USL=13.
  mean              = 10
  sample sigma      = sqrt(20/29)          = 0.830455...
  MR-bar            = 38/29                = 1.310345...
  sigma within      = MR-bar / 1.128       = 1.161654...
  Cp  = 6/(6*sw)    = 0.860845
  Cpk = 3/(3*sw)    = 0.860845
  Pp  = 6/(6*so)    = 1.203045
  Ppk = 3/(3*so)    = 1.203045
"""
import math
import unittest
from statistics import NormalDist
from unittest import mock

from app import create_app, db
from app.services import capability_service, decision_service


VALUES = [9.0, 10.0, 11.0] * 10
SIGMA_WITHIN = (38 / 29) / 1.128
SIGMA_OVERALL = math.sqrt(20 / 29)


class CapabilityIndicesTestCase(unittest.TestCase):
    def test_textbook_fixture(self):
        r = capability_service.capability_indices(VALUES, lsl=7.0, usl=13.0)
        self.assertEqual(r["n"], 30)
        self.assertAlmostEqual(r["mean"], 10.0, places=6)
        self.assertAlmostEqual(r["sigma_within"], SIGMA_WITHIN, places=6)
        self.assertAlmostEqual(r["sigma_overall"], SIGMA_OVERALL, places=6)
        self.assertAlmostEqual(r["cp"], 6 / (6 * SIGMA_WITHIN), places=3)
        self.assertAlmostEqual(r["cpk"], 3 / (3 * SIGMA_WITHIN), places=3)
        self.assertAlmostEqual(r["pp"], 6 / (6 * SIGMA_OVERALL), places=3)
        self.assertAlmostEqual(r["ppk"], 3 / (3 * SIGMA_OVERALL), places=3)
        self.assertFalse(r["one_sided"])
        self.assertFalse(r["low_confidence"])

    def test_dpmo_and_sigma_level(self):
        r = capability_service.capability_indices(VALUES, lsl=7.0, usl=13.0)
        dist = NormalDist(mu=10.0, sigma=SIGMA_OVERALL)
        expected_dpmo = (dist.cdf(7.0) + (1 - dist.cdf(13.0))) * 1_000_000
        self.assertAlmostEqual(r["dpmo"], expected_dpmo, places=1)
        # Convention: short-term sigma level = 3*Ppk + 1.5 shift.
        self.assertAlmostEqual(r["sigma_level"], 3 * r["ppk"] + 1.5, places=3)

    def test_low_confidence_below_30_points(self):
        r = capability_service.capability_indices([10.0, 11.0, 9.0], lsl=7.0, usl=13.0)
        self.assertTrue(r["low_confidence"])
        # Indices still computed — flagged, not hidden.
        self.assertIsNotNone(r["cpk"])

    def test_fewer_than_two_points_returns_none(self):
        self.assertIsNone(capability_service.capability_indices([10.0], lsl=7, usl=13))
        self.assertIsNone(capability_service.capability_indices([], lsl=7, usl=13))

    def test_one_sided_lower(self):
        r = capability_service.capability_indices(VALUES, lsl=7.0, usl=None)
        self.assertTrue(r["one_sided"])
        self.assertIsNone(r["cp"])
        self.assertIsNone(r["pp"])
        self.assertAlmostEqual(r["cpk"], 3 / (3 * SIGMA_WITHIN), places=3)
        self.assertAlmostEqual(r["ppk"], 3 / (3 * SIGMA_OVERALL), places=3)

    def test_one_sided_upper(self):
        r = capability_service.capability_indices(VALUES, lsl=None, usl=13.0)
        self.assertTrue(r["one_sided"])
        self.assertIsNone(r["cp"])
        self.assertAlmostEqual(r["cpk"], 3 / (3 * SIGMA_WITHIN), places=3)

    def test_zero_variation_returns_none(self):
        self.assertIsNone(
            capability_service.capability_indices([10.0] * 10, lsl=7, usl=13)
        )


class SpecLimitsTestCase(unittest.TestCase):
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

    def test_carbon_limits_from_element_rules_optimal_range(self):
        limits = capability_service.spec_limits_for("carbon")
        self.assertIsNotNone(limits)
        # element_rules.json: C optimal (فحص أخيرة فقط) range is 3.0..3.9
        self.assertEqual(limits["lsl"], 3.0)
        self.assertEqual(limits["usl"], 3.9)
        self.assertFalse(limits["one_sided"])

    def test_delegates_to_decision_service_resolver(self):
        """Limits must come through the decision service's own rule loader —
        never a re-implementation (spy proves the delegation)."""
        with mock.patch.object(
            decision_service, "load_element_rules",
            wraps=decision_service.load_element_rules,
        ) as spy:
            limits = capability_service.spec_limits_for("carbon")
        spy.assert_called()
        self.assertEqual((limits["lsl"], limits["usl"]), (3.0, 3.9))

    def test_tensile_is_one_sided_lower(self):
        limits = capability_service.spec_limits_for("tensile_mpa")
        self.assertIsNotNone(limits)
        self.assertTrue(limits["one_sided"])
        self.assertIsNone(limits["usl"])
        # 42.50 KgF/mm² (mechanical_rules.json) x 9.8 = 416.5 MPa
        self.assertAlmostEqual(limits["lsl"], 416.5, places=6)

    def test_characteristic_without_limits_returns_none(self):
        # microstructure_40 has no entry in element_rules and no acceptance
        # criterion in mechanical_rules, so it must stay limitless. nodularity
        # and hardness used to sit here, but both DO carry a criterion — it was
        # simply never read (see test_mechanical_capability_limits).
        self.assertIsNone(
            capability_service.spec_limits_for("microstructure_40"))


if __name__ == "__main__":
    unittest.main()
