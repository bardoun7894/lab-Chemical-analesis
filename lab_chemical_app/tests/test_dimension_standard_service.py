"""TA 1012 dimensional standard lookup — nominal, tolerance, deviation."""

import unittest

from app.services import dimension_standard_service as std


class LookupTest(unittest.TestCase):
    def test_known_dn_returns_symbols(self):
        entries = std.for_dn(300)
        self.assertIn("d1", entries)
        self.assertEqual(entries["d1"]["nominal"], 326)
        self.assertEqual(entries["d1"]["tol_plus"], 1.0)
        self.assertEqual(entries["d1"]["tol_minus"], 3.3)

    def test_limits_derived_from_nominal_and_tolerance(self):
        entries = std.for_dn(300)
        self.assertAlmostEqual(entries["d1"]["lsl"], 322.7)
        self.assertAlmostEqual(entries["d1"]["usl"], 327.0)

    def test_dn_accepts_int_float_and_string(self):
        for dn in (300, 300.0, "300", " 300 "):
            self.assertEqual(std.for_dn(dn)["d1"]["nominal"], 326, f"dn={dn!r}")

    def test_unknown_dn_is_empty_not_error(self):
        self.assertEqual(std.for_dn(9999), {})
        self.assertEqual(std.for_dn(None), {})

    def test_every_configured_dn_defines_d1_and_S1(self):
        for dn in std.available_dns():
            entries = std.for_dn(dn)
            self.assertIsNotNone(entries.get("d1", {}).get("nominal"),
                                 f"DN{dn} has no d1 nominal")
            self.assertIsNotNone(entries.get("S1", {}).get("nominal"),
                                 f"DN{dn} has no S1 nominal")

    def test_available_dns_are_numerically_ordered(self):
        dns = [int(d) for d in std.available_dns()]
        self.assertEqual(dns, sorted(dns))
        self.assertIn(1000, dns)


class LimitsForTest(unittest.TestCase):
    def test_two_sided_limit(self):
        limits = std.limits_for(300, "d1")
        self.assertAlmostEqual(limits["lsl"], 322.7)
        self.assertAlmostEqual(limits["usl"], 327.0)
        self.assertFalse(limits["one_sided"])
        self.assertIn("DN300", limits["source"])

    def test_symbol_without_tolerance_yields_no_limits(self):
        # S1 carries a nominal but no tolerance in the scanned table, so it
        # must not produce a capability index.
        self.assertIsNone(std.limits_for(300, "S1"))

    def test_unknown_symbol_or_dn(self):
        self.assertIsNone(std.limits_for(300, "nope"))
        self.assertIsNone(std.limits_for(9999, "d1"))


class EvaluateTest(unittest.TestCase):
    def test_reading_inside_tolerance(self):
        r = std.evaluate(300, "d1", 326.5)
        self.assertEqual(r["status"], "in")
        self.assertAlmostEqual(r["deviation"], 0.5)
        self.assertEqual(r["nominal"], 326)

    def test_reading_above_usl(self):
        r = std.evaluate(300, "d1", 328.0)
        self.assertEqual(r["status"], "out")
        self.assertAlmostEqual(r["deviation"], 2.0)

    def test_reading_below_lsl(self):
        r = std.evaluate(300, "d1", 322.0)
        self.assertEqual(r["status"], "out")
        self.assertAlmostEqual(r["deviation"], -4.0)

    def test_boundary_readings_are_in_tolerance(self):
        self.assertEqual(std.evaluate(300, "d1", 322.7)["status"], "in")
        self.assertEqual(std.evaluate(300, "d1", 327.0)["status"], "in")

    def test_no_tolerance_gives_deviation_but_no_verdict(self):
        r = std.evaluate(300, "S1", 7.5)
        self.assertEqual(r["status"], "no_spec")
        self.assertAlmostEqual(r["deviation"], 0.3)
        self.assertEqual(r["nominal"], 7.2)

    def test_unknown_dn_never_reports_out_of_tolerance(self):
        r = std.evaluate(9999, "d1", 1.0)
        self.assertEqual(r["status"], "no_spec")
        self.assertIsNone(r["deviation"])

    def test_blank_reading(self):
        self.assertEqual(std.evaluate(300, "d1", None)["status"], "no_spec")


class DocumentTest(unittest.TestCase):
    def test_symbols_cover_the_standard_columns(self):
        keys = std.symbol_keys()
        for expected in ("d1", "d2", "d3", "d4", "d5", "d7", "S1", "S2", "C",
                         "t1", "t2", "t3", "t4", "t5", "t6", "r1", "r2", "r3"):
            self.assertIn(expected, keys)

    def test_labels_fall_back_to_the_key(self):
        self.assertEqual(std.symbol_label("d1"), "d1 (DE)")
        self.assertEqual(std.symbol_label("unknown"), "unknown")

    def test_transcription_is_flagged_unverified(self):
        # Guards against the scanned table being trusted for Cp/Cpk before a
        # human has checked it. Flip "verified" in the JSON once reviewed.
        self.assertFalse(std.is_verified())


if __name__ == "__main__":
    unittest.main()
