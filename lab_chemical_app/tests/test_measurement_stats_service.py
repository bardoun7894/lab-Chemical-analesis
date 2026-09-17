"""Descriptive statistics over the stage JSON measurement profiles."""

import unittest

from app.services import measurement_stats_service as stats


class DescribeTest(unittest.TestCase):
    def test_basic_statistics(self):
        r = stats.describe([2, 4, 4, 4, 5, 5, 7, 9])
        self.assertEqual(r["n"], 8)
        self.assertAlmostEqual(r["mean"], 5.0)
        self.assertAlmostEqual(r["stdev"], 2.1381, places=3)  # sample (n-1)
        self.assertEqual(r["min"], 2)
        self.assertEqual(r["max"], 9)
        self.assertAlmostEqual(r["range"], 7)

    def test_single_reading_has_no_dispersion(self):
        r = stats.describe([5.0])
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["mean"], 5.0)
        self.assertIsNone(r["stdev"], "one reading must not report stdev 0")
        self.assertEqual(r["range"], 0)

    def test_empty_and_blank_input(self):
        for values in ([], None, [None, None], ["", None]):
            r = stats.describe(values)
            self.assertEqual(r["n"], 0, f"values={values!r}")
            self.assertIsNone(r["mean"])

    def test_blanks_are_dropped_not_counted_as_zero(self):
        r = stats.describe([5.0, None, 5.0, ""])
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["mean"], 5.0)

    def test_numeric_strings_are_accepted(self):
        self.assertEqual(stats.describe(["3", "5"])["mean"], 4.0)

    def test_garbage_is_ignored(self):
        self.assertEqual(stats.describe([1.0, "abc", 3.0])["n"], 2)


class ThicknessMatrixTest(unittest.TestCase):
    def test_positions_shape_is_returned_as_is(self):
        profile = {"thickness": {"positions": {"1": [5.0, 5.1, 5.2],
                                               "5.5": [4.9, 5.0, 5.0]}}}
        matrix = stats.thickness_matrix(profile)
        self.assertEqual(matrix["1"], [5.0, 5.1, 5.2])
        self.assertEqual(matrix["5.5"], [4.9, 5.0, 5.0])

    def test_legacy_flat_21_folds_into_seven_positions(self):
        flat = [float(n) for n in range(1, 22)]
        profile = {"thickness": {"samples": {"S1": flat}}}
        matrix = stats.thickness_matrix(profile)
        self.assertEqual(list(stats.THICKNESS_POSITIONS), list(matrix.keys()))
        self.assertEqual(matrix["1"], [1.0, 2.0, 3.0])
        self.assertEqual(matrix["5.5"], [16.0, 17.0, 18.0])
        self.assertEqual(matrix["6"], [19.0, 20.0, 21.0])

    def test_legacy_short_row_is_padded(self):
        profile = {"thickness": {"samples": {"S1": [1.0, 2.0]}}}
        matrix = stats.thickness_matrix(profile)
        self.assertEqual(matrix["1"], [1.0, 2.0, None])
        self.assertEqual(matrix["6"], [None, None, None])

    def test_no_thickness_data(self):
        self.assertEqual(stats.thickness_matrix({}), {})
        self.assertEqual(stats.thickness_matrix(None), {})
        self.assertEqual(stats.thickness_matrix({"diameter": {}}), {})


class ThicknessStatsTest(unittest.TestCase):
    def test_per_position_and_overall(self):
        profile = {"thickness": {"positions": {
            "1": [5.0, 5.2, 5.4],
            "2": [6.0, 6.0, 6.0],
        }}}
        result = stats.thickness_stats(profile)
        self.assertAlmostEqual(result["by_position"]["1"]["mean"], 5.2)
        self.assertAlmostEqual(result["by_position"]["2"]["stdev"], 0.0)
        self.assertEqual(result["overall"]["n"], 6)
        self.assertAlmostEqual(result["overall"]["mean"], 5.6)

    def test_unmeasured_positions_report_n_zero(self):
        profile = {"thickness": {"positions": {"1": [5.0, 5.0, 5.0]}}}
        result = stats.thickness_stats(profile)
        self.assertEqual(result["by_position"]["6"]["n"], 0)
        self.assertEqual(result["overall"]["n"], 3)

    def test_extra_position_outside_the_standard_seven_still_counts(self):
        profile = {"thickness": {"positions": {"1": [5.0], "9": [7.0]}}}
        result = stats.thickness_stats(profile)
        self.assertIn("9", result["by_position"])
        self.assertEqual(result["overall"]["n"], 2)

    def test_returns_none_without_data(self):
        self.assertIsNone(stats.thickness_stats({}))


class DiameterAndLiningTest(unittest.TestCase):
    def test_diameter_per_sample_and_overall(self):
        profile = {"diameter": {"samples": {"S1": [326.0, 326.4],
                                            "S2": [325.6, 326.0]}}}
        result = stats.diameter_stats(profile)
        self.assertAlmostEqual(result["by_sample"]["S1"]["mean"], 326.2)
        self.assertEqual(result["overall"]["n"], 4)

    def test_diameter_absent(self):
        self.assertIsNone(stats.diameter_stats({}))
        self.assertIsNone(stats.diameter_stats({"diameter": {"samples": {}}}))

    def test_lining_layers(self):
        result = stats.lining_stats(
            {"cement": [3.5, 3.9, 3.7], "coating": [70.0, 70.4, 69.2]})
        self.assertAlmostEqual(result["cement"]["mean"], 3.7)
        self.assertAlmostEqual(result["coating"]["max"], 70.4)

    def test_lining_absent_or_blank(self):
        self.assertIsNone(stats.lining_stats(None))
        self.assertIsNone(stats.lining_stats({}))
        self.assertIsNone(stats.lining_stats({"cement": [None, None]}))


if __name__ == "__main__":
    unittest.main()
