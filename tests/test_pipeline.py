import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from collector import config
from collector.client import Client
from collector.collect import RunFailed, collect_once, main as collect_main, record_run
from collector.rollup import rollup
from collector.store import Store, day_of
from collector.summarize import GAME_COLS, build_summary

T0 = 1_799_971_200  # 2027-01-15 00:00:00 UTC (a day boundary); only relative times matter


class World:
    """A tiny fake Roblox: games with stats and a few sorts."""

    def __init__(self):
        self.wall = float(T0)
        self.games = {}
        self.sorts = {"top-playing-now": [], "top-trending": [], "up-and-coming": []}
        self.fail_details = False
        self.empty_sorts = False

    def add(self, uid, playing, created=T0 - 400 * 86400, **extra):
        self.games[uid] = {
            "id": uid, "rootPlaceId": uid * 10, "name": f"Game {uid}",
            "description": "  A   test\n game  ", "creator": {"name": "dev", "type": "Group"},
            "playing": playing, "visits": playing * 1000, "maxPlayers": 10,
            "created": _iso(created), "updated": _iso(T0 - 3600),
            "genre_l1": extra.get("g1", "Simulation"), "genre_l2": extra.get("g2", "Tycoon"),
            "favoritedCount": playing * 3,
        }

    def sleep(self, seconds):
        self.wall += seconds

    def clock(self):
        return self.wall

    def at(self, seconds_after_t0):
        self.wall = float(T0 + seconds_after_t0)


def _iso(ts):
    import datetime as dt

    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.12Z")


class FakeRoblox(Client):
    def __init__(self, world, **kwargs):
        super().__init__(sleep=world.sleep, clock=world.clock, log=lambda *_: None, **kwargs)
        self.world = world

    def _fetch(self, url):
        world = self.world
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("/get-sorts"):
            sorts = [] if world.empty_sorts else [
                {
                    "sortId": sid, "sortDisplayName": sid.title(), "contentType": "Games",
                    "games": [{"universeId": u, "rootPlaceId": u * 10, "name": f"Game {u}"} for u in ids],
                }
                for sid, ids in world.sorts.items()
            ]
            return 200, {}, json.dumps({"sorts": sorts, "nextSortsPageToken": ""}).encode()
        if parsed.path.endswith("/get-sort-content"):
            return 200, {}, json.dumps({"sortId": query["sortId"][0], "games": []}).encode()
        ids = [int(x) for x in query["universeIds"][0].split(",")]
        if parsed.path.endswith("/games/votes"):
            data = [{"id": u, "upVotes": 90, "downVotes": 10} for u in ids if u in world.games]
            return 200, {}, json.dumps({"data": data}).encode()
        if parsed.path.endswith("/games"):
            assert len(ids) <= config.DETAILS_BATCH
            if world.fail_details:
                return 500, {}, b"{}"
            return 200, {}, json.dumps({"data": [world.games[u] for u in ids if u in world.games]}).encode()
        raise AssertionError(url)


def run(store, world):
    return collect_once(store, FakeRoblox(world), now=world.clock, log=lambda *_: None)


def game_rows(summary):
    return {row[0]: dict(zip(summary["cols"], row)) for row in summary["games"]}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.world = World()
        for uid, playing in ((1, 1000), (2, 500), (3, 50)):
            self.world.add(uid, playing)
        self.world.sorts["top-playing-now"] = list(range(1, 4)) + list(range(100, 130))
        for uid in range(100, 130):
            self.world.add(uid, 200)

    def test_changes_use_real_timestamps_not_assumed_gaps(self):
        world, store = self.world, self.store
        # Runs at irregular times: 0, +41min, +58min (late and early runs).
        first = run(store, world)
        world.at(41 * 60)
        world.games[1]["playing"] = 1410
        second = run(store, world)
        world.at(58 * 60)
        world.games[1]["playing"] = 1580
        world.games[2]["playing"] = 400
        third = run(store, world)
        self.assertLess(first["t"], second["t"])

        summary = build_summary(store, now=world.wall)
        self.assertEqual(summary["latest"], third["t"])
        self.assertEqual(summary["available"][:2], [True, False])  # 1h yes (within tolerance), 3h no
        rows = game_rows(summary)

        # Expected 1h reference for game 1: interpolate at latest-3600 between the real samples.
        ref_time = third["t"] - 3600
        if ref_time <= first["t"]:
            expected = 1000
        else:
            expected = 1000 + (1410 - 1000) * (ref_time - first["t"]) / (second["t"] - first["t"])
        self.assertEqual(rows[1]["r1h"], round(expected))
        self.assertEqual(rows[1]["ccu"], 1580)
        self.assertIsNone(rows[1]["r3h"])
        self.assertEqual(rows[2]["ccu"], 400)
        self.assertEqual(summary["totals"]["games"], 33)
        self.assertEqual(summary["health"]["snapshots24h"], 3)
        self.assertEqual(summary["cols"], GAME_COLS)

        # Genre rollup matches the per-game numbers.
        sim = summary["genres"]["l1"][0]
        self.assertEqual(sim["label"], "Simulation")
        self.assertEqual(sim["ccu"], sum(r["ccu"] for r in rows.values()))
        now_sum = sum(r["ccu"] for r in rows.values() if r["r1h"] is not None)
        ref_sum = sum(r["r1h"] for r in rows.values() if r["r1h"] is not None)
        self.assertAlmostEqual(sim["chg"][0], (now_sum - ref_sum) / ref_sum, places=3)

    def test_series_buckets_end_at_latest_snapshot(self):
        world, store = self.world, self.store
        for minutes in (0, 29, 63, 90, 125):
            world.at(minutes * 60)
            last = run(store, world)
        summary = build_summary(store, now=world.wall)
        hourly = summary["series"]["h"]
        self.assertEqual(hourly["end"][-1], last["t"])
        self.assertEqual(len(hourly["total"]), config.SERIES_HOURLY_POINTS)
        self.assertEqual(hourly["g"]["1"][-1], 1000)
        self.assertEqual(hourly["total"][-1], sum(g["playing"] for g in world.games.values()))
        self.assertIsNone(hourly["total"][0])

    def test_new_game_did_not_exist_at_reference_time(self):
        world, store = self.world, self.store
        run(store, world)
        world.at(2 * 3600)
        world.add(7, 300, created=T0 + 3600 + 600)  # created after "latest - 1h"
        world.sorts["up-and-coming"] = [7]
        world.add(8, 300, created=T0 - 86400)  # older game that is merely discovered now
        world.sorts["top-trending"] = [8]
        run(store, world)
        rows = game_rows(build_summary(store, now=world.wall))
        self.assertEqual(rows[7]["r1h"], 0)      # true zero: it did not exist
        self.assertIsNone(rows[8]["r1h"])        # unknown: existed but was not tracked

    def test_sort_entries_are_not_counted_on_first_observation(self):
        world, store = self.world, self.store
        world.sorts["top-trending"] = [1]
        run(store, world)
        world.at(1800)
        world.sorts["top-trending"] = [1, 2]
        run(store, world)
        summary = build_summary(store, now=world.wall)
        rows = game_rows(summary)
        ids = [s["id"] for s in summary["sorts"]]
        trending = ids.index("top-trending")
        self.assertEqual(rows[1]["entries"], [])
        self.assertEqual([e[0] for e in rows[2]["entries"]], [trending])
        self.assertIn([trending, 2], rows[2]["sorts"])
        self.assertEqual(summary["genres"]["l1"][0]["ent"][0], 1)

    def test_failed_sorts_save_nothing_and_are_flagged(self):
        world, store = self.world, self.store
        world.empty_sorts = True
        with self.assertRaises(RunFailed):
            run(store, world)
        self.assertEqual(store.snapshot_days(), [])
        record_run(store, {"at": T0, "ok": False, "t": None}, RunFailed("explore sorts came back empty"))
        status = store.load_status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["failStreak"], 1)
        self.assertIn("empty", status["lastError"]["message"])
        summary = build_summary(store, now=world.wall)
        self.assertTrue(summary["empty"])
        self.assertFalse(summary["health"]["ok"])

    def test_failed_stats_do_not_overwrite_good_data(self):
        world, store = self.world, self.store
        good = run(store, world)
        games_before = Path(self.tmp.name, "games.json").read_text()
        world.at(1800)
        world.fail_details = True
        with self.assertRaises(RunFailed):
            collect_once(store, FakeRoblox(world, max_attempts=2), now=world.clock, log=lambda *_: None)
        self.assertEqual(len(store.load_snapshots()), 1)
        self.assertEqual(Path(self.tmp.name, "games.json").read_text(), games_before)
        self.assertEqual(build_summary(store, now=world.wall)["latest"], good["t"])

    def test_cold_games_are_polled_less_often(self):
        world, store = self.world, self.store
        run(store, world)
        world.sorts["top-playing-now"] = [1, 2] + list(range(100, 130))  # game 3 (50 players) left the sorts
        polled = []
        for day in range(8, 10):
            for half_hour in range(0, 48):
                world.at(day * 86400 + half_hour * 1800)
                run(store, world)
                snap = store.load_snapshots(since=int(world.wall) - 600)[-1]
                polled.append(3 in [row[0] for row in snap["g"]])
        self.assertTrue(any(polled))
        self.assertLessEqual(sum(polled), 10)  # roughly every 6 hours instead of 96 times

    def test_rollup_and_retention(self):
        world, store = self.world, self.store
        days = config.RAW_RETENTION_DAYS + 3
        for day in range(days):
            for hour in (1, 7, 13, 19):
                world.at(day * 86400 + hour * 3600 + (day * 137) % 900)
                world.games[1]["playing"] = 1000 + day * 10
                run(store, world)
        result = rollup(store, log=lambda *_: None)
        first_day = day_of(T0)
        self.assertIn(first_day, result["rolled"])
        self.assertIn(first_day, result["deleted"])
        self.assertEqual(len(store.daily_days()), days - 1)       # every finished day
        self.assertEqual(len(store.snapshot_days()), config.RAW_RETENTION_DAYS + 1)
        daily = store.load_daily(first_day)
        entry = dict(zip(daily["cols"], daily["g"]["1"]))
        # The day's last hours ramp toward the next day's first sample (1010).
        self.assertAlmostEqual(entry["avg"], 1000, delta=2)
        self.assertEqual(entry["peak"], 1000)
        self.assertGreater(entry["covH"], 12)
        again = rollup(store, log=lambda *_: None)
        self.assertEqual(again, {"rolled": [], "deleted": []})
        summary = build_summary(store, now=world.wall)
        daily_series = summary["series"]["d"]
        self.assertEqual(len(daily_series["days"]), days - 1)
        self.assertAlmostEqual(daily_series["g"]["1"][0], 1000, delta=2)
        self.assertTrue(all(summary["available"]))
        self.assertEqual(game_rows(summary)[1]["r7d"], 1000 + (days - 8) * 10)

    def test_main_exit_code_flags_failure(self):
        # No network in tests: point the collector at a closed port.
        original = config.EXPLORE_API
        config.EXPLORE_API = "http://127.0.0.1:9/none"
        try:
            from collector import client as client_module

            real_init = client_module.Client.__init__

            def fast_init(self, **kwargs):
                kwargs.update(min_interval=0, max_attempts=1, sleep=lambda s: None)
                real_init(self, **kwargs)

            client_module.Client.__init__ = fast_init
            try:
                code = collect_main(["--data", self.tmp.name])
            finally:
                client_module.Client.__init__ = real_init
        finally:
            config.EXPLORE_API = original
        self.assertEqual(code, 3)
        status = self.store.load_status()
        self.assertFalse(status["ok"])
        self.assertEqual(self.store.snapshot_days(), [])


if __name__ == "__main__":
    unittest.main()
