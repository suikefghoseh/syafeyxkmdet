"""Files in the data directory.

    games.json                  slow-changing facts per game (name, description, dates, genre)
    state.json                  fast-changing bookkeeping per game and per sort
    status.json                 health of the collector and a short run log
    scout.json                  untracked games the scout has ruled out, and its place in the rotation
    snapshots/YYYY-MM-DD/HHMMSS.json   one file per successful run
    daily/YYYY-MM-DD.json       one rollup per finished day; raw snapshots are deleted after the retention period
    summary.json                precomputed numbers for readers
"""

import datetime as dt
import json
import os
import shutil
from pathlib import Path

UTC = dt.timezone.utc
SNAPSHOT_COLS = ["id", "ccu", "visits", "up", "down", "favs"]


def day_of(ts):
    return dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def day_start(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _dump_keyed(head, key, records):
    """One record per line, sorted by numeric id: small diffs between versions."""
    lines = [f"{_dumps(k)}:{_dumps(records[k])}" for k in sorted(records, key=int)]
    return _dumps(head)[:-1] + f',"{key}":{{\n' + ",\n".join(lines) + "\n}}\n"


class Store:
    def __init__(self, root):
        self.root = Path(root)

    # -- generic ------------------------------------------------------------
    def read(self, name, default):
        path = self.root / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, name, obj):
        _write_text(self.root / name, _dumps(obj) + "\n")

    # -- registry -----------------------------------------------------------
    def load_games(self):
        return self.read("games.json", {"v": 1, "games": {}})["games"]

    def save_games(self, games):
        _write_text(self.root / "games.json", _dump_keyed({"v": 1}, "games", games))

    def load_state(self):
        state = self.read("state.json", {"v": 1})
        state.setdefault("first", None)
        state.setdefault("sorts", {})
        state.setdefault("games", {})
        return state

    def save_state(self, state):
        head = {k: v for k, v in state.items() if k != "games"}
        _write_text(self.root / "state.json", _dump_keyed(head, "games", state["games"]))

    def load_scout(self):
        cache = self.read("scout.json", {"v": 1})
        cache.pop("cursor", None)        # the first version kept one place for both kinds of listing
        cache.setdefault("charts", 0)    # place in the rotation of country charts
        cache.setdefault("searches", 0)  # place in the rotation of searches
        cache.setdefault("gap", 0)       # extra seconds between searches that the search last tolerated
        cache.setdefault("seen", {})
        return cache

    def save_scout(self, cache):
        head = {k: v for k, v in cache.items() if k != "seen"}
        _write_text(self.root / "scout.json", _dump_keyed(head, "seen", cache["seen"]))

    def load_status(self):
        status = self.read("status.json", {"v": 1})
        status.setdefault("ok", None)
        status.setdefault("lastAttempt", None)
        status.setdefault("lastOk", None)
        status.setdefault("failStreak", 0)
        status.setdefault("lastError", None)
        status.setdefault("runs", [])
        return status

    # -- snapshots ----------------------------------------------------------
    def write_snapshot(self, snapshot):
        stamp = dt.datetime.fromtimestamp(snapshot["t"], UTC)
        name = f"snapshots/{stamp:%Y-%m-%d}/{stamp:%H%M%S}.json"
        self.write(name, snapshot)
        return name

    def snapshot_days(self):
        base = self.root / "snapshots"
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def load_snapshots(self, since=None, until=None, days=None):
        """Snapshots ordered by time. `since`/`until` are unix seconds (inclusive)."""
        out = []
        for day in days if days is not None else self.snapshot_days():
            start = day_start(day)
            if since is not None and start + 86400 < since:
                continue
            if until is not None and start > until:
                continue
            for path in sorted((self.root / "snapshots" / day).glob("*.json")):
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                t = snapshot.get("t")
                if not isinstance(t, int):
                    continue
                if (since is None or t >= since) and (until is None or t <= until):
                    out.append(snapshot)
        out.sort(key=lambda s: s["t"])
        return out

    def delete_snapshot_day(self, day):
        shutil.rmtree(self.root / "snapshots" / day, ignore_errors=True)

    # -- daily rollups ------------------------------------------------------
    def load_daily(self, day):
        return self.read(f"daily/{day}.json", None)

    def save_daily(self, day, data):
        self.write(f"daily/{day}.json", data)

    def daily_days(self):
        base = self.root / "daily"
        if not base.is_dir():
            return []
        return sorted(p.stem for p in base.glob("*.json"))
