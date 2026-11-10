import unittest

from collector.timeseries import bucket_stats, interp, last_before, peak_between


class InterpTest(unittest.TestCase):
    def test_exact_and_between_irregular_samples(self):
        ts, vs = [0, 600, 4200], [100, 160, 400]  # gaps of 10 and 60 minutes
        self.assertEqual(interp(ts, vs, 600, 7200), 160)
        self.assertAlmostEqual(interp(ts, vs, 300, 7200), 130)
        self.assertAlmostEqual(interp(ts, vs, 2400, 7200), 280)  # halfway through the long gap

    def test_outside_range_is_unknown(self):
        ts, vs = [1000, 2000], [5, 7]
        self.assertIsNone(interp(ts, vs, 900, 7200))
        self.assertIsNone(interp(ts, vs, 2100, 7200))
        self.assertEqual(interp(ts, vs, 900, 7200, edge_tol=100), 5)
        self.assertEqual(interp(ts, vs, 2100, 7200, edge_tol=100), 7)
        self.assertIsNone(interp([], [], 5, 7200))

    def test_gap_wider_than_max_gap_is_unknown(self):
        ts, vs = [0, 10000], [1, 2]
        self.assertIsNone(interp(ts, vs, 5000, max_gap=7200))
        self.assertEqual(interp(ts, vs, 100, max_gap=7200, edge_tol=200), 1)
        self.assertEqual(interp(ts, vs, 9900, max_gap=7200, edge_tol=200), 2)
        self.assertAlmostEqual(interp(ts, vs, 5000, max_gap=20000), 1.5)


class BucketStatsTest(unittest.TestCase):
    def test_mean_is_time_weighted_not_sample_weighted(self):
        # 10s at 0, then a 50s ramp from 0 to 100. Plain sample mean would be 33.3.
        means, covered = bucket_stats([0, 10, 60], [0, 0, 100], [0, 60], max_gap=1000)
        self.assertAlmostEqual(means[0], (0 * 10 + 50 * 50) / 60)
        self.assertEqual(covered[0], 60)

    def test_segments_are_split_across_buckets(self):
        means, covered = bucket_stats([0, 100], [0, 100], [0, 50, 100], max_gap=1000)
        self.assertAlmostEqual(means[0], 25)
        self.assertAlmostEqual(means[1], 75)
        self.assertEqual(covered, [50, 50])

    def test_samples_outside_the_range_shape_the_edges(self):
        means, covered = bucket_stats([-50, 150], [0, 200], [0, 100], max_gap=1000)
        self.assertAlmostEqual(means[0], 100)
        self.assertEqual(covered[0], 100)

    def test_wide_gap_is_not_covered(self):
        means, covered = bucket_stats([0, 100, 5000, 5100], [10, 10, 90, 90], [0, 2500, 5100], max_gap=1000)
        self.assertAlmostEqual(means[0], 10)
        self.assertEqual(covered[0], 100)
        self.assertAlmostEqual(means[1], 90)
        self.assertEqual(covered[1], 100)

    def test_isolated_sample_and_empty_bucket(self):
        means, covered = bucket_stats([50], [7], [0, 100, 200], max_gap=1000)
        self.assertEqual(means, [7, None])
        self.assertEqual(covered, [0, 0])

    def test_last_bucket_includes_a_sample_on_its_end_edge(self):
        means, _ = bucket_stats([200], [9], [0, 100, 200], max_gap=1000)
        self.assertEqual(means, [None, 9])

    def test_helpers(self):
        ts, vs = [10, 20, 30], [5, 9, 7]
        self.assertEqual(peak_between(ts, vs, 10, 30), 9)
        self.assertIsNone(peak_between(ts, vs, 31, 40))
        self.assertEqual(last_before(ts, vs, 30), 9)
        self.assertIsNone(last_before(ts, vs, 10))


if __name__ == "__main__":
    unittest.main()
