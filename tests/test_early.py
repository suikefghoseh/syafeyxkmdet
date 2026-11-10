import math
import tempfile
import unittest

from collector import config, early, scout
from collector.collect import record_run
from collector.store import Store
from collector.summarize import build_summary
from tests.test_pipeline import T0, World, game_rows, run


class EarlyRuleTest(unittest.TestCase):
    def test_many_players_for_few_visits(self):
        self.assertTrue(early.is_early(500, 3_000))          # 500 players on a few thousand visits
        self.assertFalse(early.is_early(500, 5_000_000))     # same crowd, long history
        self.assertFalse(early.is_early(10, 20))             # too few players to mean anything
        self.assertFalse(early.is_early(None, 20))
        self.assertAlmostEqual(early.ratio(500, 3_000), 1 / 6)
        self.assertEqual(early.ratio(0, 3_000), 0.0)

    def test_a_lagging_visit_counter_cannot_push_the_ratio_past_one(self):
        self.assertEqual(early.ratio(500, 0), 1.0)
        self.assertEqual(early.ratio(500, None), 1.0)
        self.assertEqual(early.ratio(500, 120), 1.0)


class ScoutPlanTest(unittest.TestCase):
    def test_trend_words_are_shared_by_several_catches(self):
        names = ["DONT SPLASH", "+1 Dont Splash 🌊", "Splash Together [NEW]", "Dig & Collect"]
        # "dont", "new", "dig" and "collect" are searched anyway; "together" is a keyword too.
        self.assertEqual(scout.trend_words(names), ["splash"])
        self.assertEqual(scout.trend_words(names, limit=0), [])

    def test_plan_is_pinned_words_then_trend_words_then_a_slice_of_the_rotation(self):
        queries, from_rotation = scout.plan(0, ["Splash One", "Splash Two"])
        pinned = [("search", word) for word in config.SCOUT_PINNED]
        self.assertEqual(queries[: len(pinned) + 1], pinned + [("search", "splash")])
        self.assertEqual(len(queries), config.SCOUT_QUERIES_PER_RUN)
        self.assertEqual(queries[len(pinned) + 1], scout.rotation()[0])
        self.assertEqual(from_rotation, config.SCOUT_QUERIES_PER_RUN - len(pinned) - 1)
        wrapped, _ = scout.plan(len(scout.rotation()) - 1, [])
        self.assertEqual(wrapped[len(pinned) :][:2], [scout.rotation()[-1], scout.rotation()[0]])

    def test_labels(self):
        self.assertEqual(scout.label(("search", "release")), "search:release")
        self.assertEqual(scout.label(("chart", "up-and-coming", "ph")), "chart:up-and-coming@ph")


class ScoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.world = World()
        self.world.sorts["top-playing-now"] = list(range(100, 130))
        for uid in range(100, 130):
            self.world.add(uid, 200)

    def test_spiking_game_outside_every_chart_is_caught(self):
        world, store = self.world, self.store
        world.add(900, 500, visits=3_000)        # made 400 days ago, suddenly 500 players
        world.add(901, 500, visits=5_000_000)    # same crowd on a long history
        world.add(902, 20, visits=100)           # too small to mean anything
        world.search["release"] = [900, 901, 902, 100]
        result = run(store, world)

        games = store.load_games()
        self.assertEqual(games["900"]["src"], "search:release")
        self.assertEqual(games["900"]["n"], "Game 900")
        self.assertNotIn("901", games)
        self.assertNotIn("902", games)
        self.assertEqual(result["scout"]["added"], 1)
        self.assertEqual(result["scout"]["checked"], 2)      # 902 and 100 never cost a stats request
        self.assertFalse(result["partial"])
        row = {r[0]: r for r in store.load_snapshots()[-1]["g"]}[900]
        self.assertEqual(row, [900, 500, 3_000, 90, 10, 1_500])

        record_run(store, {"at": T0, "ok": True, "t": result["t"], "scout": result["scout"]})
        summary = build_summary(store, now=world.wall)
        caught = game_rows(summary)[900]
        self.assertEqual(caught["src"], "search:release")
        self.assertEqual((caught["earlyAt"], caught["earlyCcu"], caught["earlyVisits"]), (result["t"], 500, 3_000))
        self.assertEqual(caught["earlyLast"], result["t"])
        self.assertGreater(summary["latest"] - caught["created"], config.NEW_GAME_DAYS * 86400)  # not "new" by age
        self.assertEqual(summary["early"]["now"], 1)
        self.assertEqual(summary["early"]["scouted"], 1)
        self.assertEqual(summary["early"]["minCcu"], config.EARLY_MIN_CCU)
        self.assertEqual(summary["early"]["scout"]["added24h"], 1)
        self.assertEqual(summary["early"]["scout"]["listings"], config.SCOUT_QUERIES_PER_RUN)

    def test_ruled_out_game_is_only_asked_about_again_once_it_could_pass(self):
        world, store = self.world, self.store
        world.add(901, 500, visits=5_000_000)
        world.search["release"] = [901]
        run(store, world)
        world.at(1800)
        run(store, world)
        self.assertEqual(world.asked.count(901), 1)
        self.assertIn("901", store.load_scout()["seen"])

        world.at(3600)
        world.games[901].update(playing=6_000, visits=5_050_000)  # 1.2 players per 1,000 visits
        run(store, world)
        self.assertEqual(world.asked.count(901), 2)
        self.assertEqual(store.load_games()["901"]["src"], "search:release")
        self.assertNotIn("901", store.load_scout()["seen"])

    def test_country_charts_are_scouted_too(self):
        world, store = self.world, self.store
        world.add(910, 300, visits=40_000)
        world.country_sorts[("up-and-coming", "ph")] = [910]
        run(store, world)
        self.assertEqual(store.load_games()["910"]["src"], "chart:up-and-coming@ph")
        self.assertNotIn("s", store.load_state()["games"]["910"])  # a country chart is not a main chart

    def test_every_listing_gets_its_turn(self):
        world, store = self.world, self.store
        per_run = config.SCOUT_QUERIES_PER_RUN - len(config.SCOUT_PINNED)
        runs = math.ceil(len(scout.rotation()) / per_run)
        for i in range(runs):
            world.at(i * 1800)
            run(store, world)
        self.assertEqual(set(scout.rotation()) - set(world.listings), set())
        for word in config.SCOUT_PINNED:
            self.assertEqual(world.listings.count(("search", word)), runs)

    def test_catch_is_recorded_once_and_followed_as_the_game_grows(self):
        world, store = self.world, self.store
        world.add(900, 500, visits=3_000)
        world.search["new"] = [900]
        first = run(store, world)
        world.at(1800)
        world.games[900].update(playing=4_000, visits=60_000)
        second = run(store, world)
        world.at(2 * 86400)
        world.games[900].update(playing=20_000, visits=30_000_000)  # grown up: no longer early
        third = run(store, world)

        summary = build_summary(store, now=world.wall)
        caught = game_rows(summary)[900]
        self.assertEqual(summary["latest"], third["t"])
        self.assertEqual((caught["earlyAt"], caught["earlyCcu"], caught["earlyVisits"]), (first["t"], 500, 3_000))
        self.assertEqual(caught["earlyLast"], second["t"])
        self.assertEqual(caught["ccu"], 20_000)
        self.assertEqual(summary["early"]["now"], 0)
        self.assertEqual(summary["early"]["scouted"], 1)

    def test_chart_games_are_caught_by_the_same_rule(self):
        world, store = self.world, self.store
        world.add(7, 800, visits=90_000, created=T0 - 200 * 86400)
        world.sorts["top-trending"] = [7]
        result = run(store, world)
        summary = build_summary(store, now=world.wall)
        caught = game_rows(summary)[7]
        self.assertEqual(caught["src"], "")
        self.assertEqual((caught["earlyAt"], caught["earlyCcu"]), (result["t"], 800))
        self.assertIsNone(game_rows(summary)[100]["earlyAt"])
        self.assertEqual((summary["early"]["now"], summary["early"]["scouted"]), (1, 0))

    def test_small_catch_keeps_being_polled_every_run(self):
        world, store = self.world, self.store
        world.add(900, 60, visits=20_000)        # under HOT_CCU and not a new game
        world.search["new"] = [900]
        run(store, world)
        world.search["new"] = []
        for step in range(3):
            world.at(10 * 86400 + step * 1800)
            run(store, world)
            snap = store.load_snapshots(since=int(world.wall) - 600)[-1]
            self.assertIn(900, [row[0] for row in snap["g"]])

    def test_scout_trouble_never_costs_the_snapshot(self):
        world, store = self.world, self.store
        world.add(900, 500, visits=3_000)
        world.search["new"] = [900]
        world.fail_search = True
        result = run(store, world)
        self.assertFalse(result["partial"])
        self.assertEqual(result["scout"]["added"], 0)
        self.assertLess(result["scout"]["q"], config.SCOUT_QUERIES_PER_RUN)  # gave up on its time budget
        self.assertEqual(len(store.load_snapshots()), 1)

        world.at(1800)
        world.fail_search, world.odd_search = False, True
        result = run(store, world)
        self.assertIn("error", result["scout"])
        self.assertEqual(len(store.load_snapshots()), 2)

        world.at(3600)
        world.odd_search, world.limit_search = False, True
        asked = len(world.listings)
        result = run(store, world)
        self.assertEqual(len(set(world.listings[asked:])), 1)  # backs off after the first rate-limited listing
        self.assertEqual(len(store.load_snapshots()), 3)

        world.at(5400)
        world.limit_search = False
        self.assertEqual(run(store, world)["scout"]["added"], 1)

    def test_faded_scout_games_are_dropped_but_charted_ones_stay(self):
        world, store = self.world, self.store
        world.add(900, 500, visits=3_000)
        world.add(901, 400, visits=2_000)
        world.search["new"] = [900, 901]
        run(store, world)
        world.search["new"] = []
        world.games[900]["playing"] = 0
        world.games[901]["playing"] = 0
        world.sorts["top-trending"] = [901]
        world.at(1800)
        run(store, world)
        world.sorts["top-trending"] = []

        world.at((config.SCOUT_DROP_DAYS - 1) * 86400)
        run(store, world)
        self.assertIn("900", store.load_games())
        world.at((config.SCOUT_DROP_DAYS + 1) * 86400)
        result = run(store, world)
        self.assertNotIn("900", store.load_games())
        self.assertNotIn("900", store.load_state()["games"])
        self.assertIn("901", store.load_games())
        self.assertEqual(result["scout"]["dropped"], 1)
        self.assertNotIn(900, game_rows(build_summary(store, now=world.wall)))


if __name__ == "__main__":
    unittest.main()
