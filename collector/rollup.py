"""Daily rollups and raw-data retention.

Usage: python -m collector.rollup --data DIR

Every finished UTC day gets one small file with per-game daily numbers.
Raw snapshots older than RAW_RETENTION_DAYS are deleted once their day has
been rolled up, which keeps the data directory at a steady size.
"""

import argparse
import os
import sys

from . import config
from .store import Store, day_of, day_start
from .timeseries import bucket_stats, peak_between

DAILY_COLS = ["avg", "peak", "covH", "visits", "up", "down", "favs"]


def build_day(store, day):
    """Rollup for one UTC day, or None if the day has no snapshots."""
    a = day_start(day)
    b = a + 86400
    # Neighbouring samples just outside the day let the edges be interpolated.
    snaps = store.load_snapshots(since=a - config.LONG_GAP_S, until=b + config.LONG_GAP_S)
    inside = [s for s in snaps if a <= s["t"] < b]
    if not inside:
        return None

    series = {}
    for snap in snaps:
        t = snap["t"]
        for gid, ccu, visits, up, down, favs in snap["g"]:
            s = series.get(gid)
            if s is None:
                s = series[gid] = {"t": [], "ccu": [], "last": None}
            s["t"].append(t)
            s["ccu"].append(ccu)
            if a <= t < b:
                s["last"] = (visits, up, down, favs)

    games, total_area = {}, 0.0
    for gid, s in series.items():
        if s["last"] is None:
            continue
        means, covered = bucket_stats(s["t"], s["ccu"], [a, b], config.LONG_GAP_S)
        if means[0] is None:
            continue
        total_area += means[0] * covered[0]
        visits, up, down, favs = s["last"]
        games[str(gid)] = [
            round(means[0]),
            peak_between(s["t"], s["ccu"], a, b),
            round(covered[0] / 3600, 1),
            visits, up, down, favs,
        ]
    return {
        "v": 1,
        "day": day,
        "n": len(inside),
        "span": [inside[0]["t"], inside[-1]["t"]],
        "total": round(total_area / 86400),
        "cols": DAILY_COLS,
        "g": games,
    }


def rollup(store, log=print):
    days = store.snapshot_days()
    if not days:
        return {"rolled": [], "deleted": []}
    done = set(store.daily_days())
    rolled, deleted = [], []

    for day in days[:-1]:  # the newest day may still be in progress
        if day in done:
            continue
        data = build_day(store, day)
        if data is None:
            continue
        store.save_daily(day, data)
        done.add(day)
        rolled.append(day)
        log(f"rolled up {day}: {len(data['g'])} games from {data['n']} snapshots")

    newest = store.load_snapshots(days=[days[-1]])
    if newest:
        cutoff = day_of(newest[-1]["t"] - config.RAW_RETENTION_DAYS * 86400)
        for day in days:
            if day < cutoff and day in done:
                store.delete_snapshot_day(day)
                deleted.append(day)
                log(f"deleted raw snapshots of {day}")
    return {"rolled": rolled, "deleted": deleted}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="data directory")
    args = parser.parse_args(argv)
    result = rollup(Store(args.data))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"rolled={'true' if result['rolled'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
