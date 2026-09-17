"""Lab (mechanical) characteristics get capability limits from the rules config.

Every one of these criteria was already configured in mechanical_rules.json and
already used by the decision engine to accept or reject a test — but
capability_service only ever implemented tensile, so the capability report
showed elongation, hardness, nodularity and the microstructure percentages as
"no limits" and refused to compute Cp/Cpk for them. DrAlaa read that as "the
lab things aren't entering the report".
"""

import unittest

from app import create_app, db
from app.services import capability_service, mechanical_decision_service
from app.services import spc_service


class MechanicalLimitsTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.criteria = (
            mechanical_decision_service.load_mechanical_config()
            .get("acceptance_criteria", {})
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_elongation_gets_a_floor(self):
        limits = capability_service.spec_limits_for("elongation")
        self.assertIsNotNone(limits)
        self.assertEqual(limits["lsl"], 10.0)
        self.assertIsNone(limits["usl"])
        self.assertTrue(limits["one_sided"])

    def test_hardness_gets_a_ceiling_not_a_floor(self):
        limits = capability_service.spec_limits_for("hardness")
        self.assertIsNotNone(limits)
        self.assertIsNone(limits["lsl"], "hardness has no minimum in the rules")
        self.assertEqual(limits["usl"], 230.0)

    def test_nodularity_and_ferrite(self):
        self.assertEqual(
            capability_service.spec_limits_for("nodularity")["lsl"], 85.0)
        self.assertEqual(
            capability_service.spec_limits_for("microstructure_70")["lsl"], 70.0)

    def test_tensile_in_kgf_uses_the_unconverted_threshold(self):
        limits = capability_service.spec_limits_for("tensile_strength")
        self.assertAlmostEqual(limits["lsl"], 42.5)
        # The MPa characteristic keeps its conversion.
        self.assertAlmostEqual(
            capability_service.spec_limits_for("tensile_mpa")["lsl"],
            42.5 * capability_service.KGF_TO_MPA)

    def test_nodule_count_and_carbides_are_now_chartable(self):
        for key in ("nodule_count", "carbides"):
            self.assertIn(key, spc_service.CHARACTERISTICS)
            self.assertTrue(spc_service.is_mechanical(key))
        self.assertEqual(
            capability_service.spec_limits_for("nodule_count")["lsl"], 400.0)
        self.assertEqual(
            capability_service.spec_limits_for("carbides")["usl"], 1.0)

    def test_source_names_the_criterion(self):
        source = capability_service.spec_limits_for("hardness")["source"]
        self.assertIn("mechanical_rules", source)
        self.assertIn("hardness", source)

    def test_characteristic_without_a_criterion_stays_limitless(self):
        # %>40 has no acceptance criterion; it must not borrow one.
        self.assertNotIn("microstructure_40", self.criteria)
        self.assertIsNone(
            capability_service.spec_limits_for("microstructure_40"))

    def test_every_configured_criterion_is_reachable(self):
        """Guards against a criterion being added to the config and silently
        never surfacing because nothing maps it to a characteristic."""
        mapped = {key for key, _side
                  in capability_service.MECHANICAL_CRITERION.values()}
        for key in self.criteria:
            self.assertIn(key, mapped, f"criterion '{key}' maps to nothing")

    def test_unparseable_condition_yields_no_limit(self):
        original = mechanical_decision_service.load_mechanical_config

        def broken():
            return {"acceptance_criteria": {"hardness": {"condition": "nonsense"}}}

        mechanical_decision_service.load_mechanical_config = broken
        try:
            self.assertIsNone(capability_service.spec_limits_for("hardness"))
        finally:
            mechanical_decision_service.load_mechanical_config = original

    def test_the_lab_is_no_longer_mostly_disabled(self):
        """The symptom DrAlaa reported: 1 of 7 mechanical characteristics had
        limits. All but microstructure_40 should now resolve."""
        resolvable = [
            key for key in spc_service.MECHANICAL_CHARACTERISTICS
            if capability_service.spec_limits_for(key) is not None
        ]
        self.assertEqual(len(resolvable), 8, sorted(resolvable))
        self.assertNotIn("microstructure_40", resolvable)


if __name__ == "__main__":
    unittest.main()
