"""Build summary.json: everything a reader needs, precomputed.

Usage: python -m collector.summarize --data DIR

Readers never have to open raw snapshots. All changes are computed against
the value at exactly "latest snapshot time minus window", interpolated from
the real sample timestamps, so late or missing runs do not distort them.
"""

import argparse
import sys
import time

from . import config
from .early import is_early
from .groupstats import group_stats
from .store import Store
from .timeseries import bucket_stats, interp

GAME_COLS = [
    "id", "name", "place", "g1", "g2", "creator", "created", "updated", "firstSeen",
    "ccu", "sampleAge",
    "r1h", "r3h", "r6h", "r12h", "r24h", "r48h", "r7d",
    "visits", "visitsRef24h", "visitsRef7d",
    "up", "down", "upRef24h", "downRef24h",
    "favs", "favsRef24h",
    "peak7d", "peakAll", "sorts", "entries",
    # How the scout found the game (empty for chart games), then the early
    # catch: when it first met the rule, players and visits then, and when it
    # last met it.
    "src", "earlyAt", "earlyCcu", "earlyVisits", "earlyLast",
]


def _tolerance(window):
    return min(window * 0.25, 3600)


def _max_gap(window):
    return config.SHORT_GAP_S if window <= 3 * 3600 else config.LONG_GAP_S


def load_series(snaps):
    """Per-game sample lists from the snapshots (already ordered by time)."""
    series = {}
    for snap in snaps:
        t = snap["t"]
        for gid, ccu, visits, up, down, favs in snap["g"]:
            s = series.get(gid)
            if s is None:
                s = series[gid] = {k: [] for k in ("t", "ccu", "vt", "visits", "ft", "favs", "ut", "up", "down")}
            s["t"].append(t)
            s["ccu"].append(ccu)
            if visits is not None:
                s["vt"].append(t)
                s["visits"].append(visits)
            if favs is not None:
                s["ft"].append(t)
                s["favs"].append(favs)
            if up is not None and down is not None:
                s["ut"].append(t)
                s["up"].append(up)
                s["down"].append(down)
    return series


def _ref(ts, vs, at, window, created, available):
    """Value `window` seconds before the latest snapshot.

    None when unknown. 0 when the game did not exist yet at that time.
    """
    if not available:
        return None
    value = interp(ts, vs, at, _max_gap(window), _tolerance(window))
    if value is None and created is not None and created > at:
        return 0
    return None if value is None else round(value)


def health_block(status, snaps, latest, first):
    day_ago = latest - 86400
    recent = [s["t"] for s in snaps if s["t"] >= day_ago]
    gaps = sorted((b - a) / 60 for a, b in zip(recent, recent[1:]))
    runs = [r for r in status.get("runs") or [] if r.get("at", 0) >= day_ago]
    return {
        "ok": status.get("ok"),
        "lastAttempt": status.get("lastAttempt"),
        "lastOk": status.get("lastOk"),
        "failStreak": status.get("failStreak", 0),
        "lastError": status.get("lastError"),
        "runs24h": len(runs),
        "fails24h": sum(1 for r in runs if not r.get("ok")),
        "partial24h": sum(1 for r in runs if r.get("partial")),
        "snapshots24h": len(recent),
        "medianGapMin24h": round(gaps[len(gaps) // 2], 1) if gaps else None,
        "maxGapMin24h": round(gaps[-1], 1) if gaps else None,
        "firstSnapshot": first,
    }


def early_block(status, rows, latest):
    """The early rule in force, how many games meet it now, and what the scout did."""
    ccu, visits, src = GAME_COLS.index("ccu"), GAME_COLS.index("visits"), GAME_COLS.index("src")
    runs = [r for r in status.get("runs") or [] if r.get("ok") and isinstance(r.get("scout"), dict)]
    recent = [r for r in runs if r.get("at", 0) >= latest - 86400]
    last = runs[-1]["scout"] if runs else {}
    return {
        "minCcu": config.EARLY_MIN_CCU,
        "minPerThousandVisits": config.EARLY_MIN_RATIO * 1000,
        "now": sum(1 for r in rows if is_early(r[ccu], r[visits])),
        "scouted": sum(1 for r in rows if r[src]),
        "scout": {
            "lastAt": runs[-1].get("at") if runs else None,
            "listings": last.get("q"),
            "listed": last.get("listed"),
            "checked": last.get("checked"),
            "added": last.get("added"),
            "error": last.get("error"),
            "runs24h": len(recent),
            "added24h": sum(r["scout"].get("added") or 0 for r in recent),
        },
    }


def build_summary(store, now=None):
    games = store.load_games()
    state = store.load_state()
    status = store.load_status()
    generated = int(time.time() if now is None else now)
    windows = config.WINDOWS
    window_seconds = [w for _, w in windows]

    days = store.snapshot_days()
    newest = store.load_snapshots(days=days[-1:]) if days else []
    if not newest:
        return {
            "v": 1, "generated": generated, "latest": None, "empty": True,
            "health": health_block(status, [], generated, state.get("first")),
        }
    latest = newest[-1]["t"]
    snaps = store.load_snapshots(since=latest - config.SUMMARY_LOOKBACK_S, until=latest)
    first_loaded = snaps[0]["t"]
    first_ever = state.get("first") or first_loaded
    series = load_series(snaps)

    available = [first_loaded <= latest - w + _tolerance(w) for w in window_seconds]
    ref_at = [latest - w for w in window_seconds]
    i24 = [name for name, _ in windows].index("24h")
    i7d = [name for name, _ in windows].index("7d")

    # Sorts: current membership comes from the latest snapshot.
    current = newest[-1].get("s") or {}
    sort_ids = sorted(
        (sid for sid, info in state["sorts"].items() if latest - info.get("last", 0) <= 7 * 86400),
        key=lambda sid: (sid not in config.SIGNAL_SORTS, "@" in sid, sid),
    )
    sort_index = {sid: i for i, sid in enumerate(sort_ids)}
    sorts_out = [
        {
            "id": sid,
            "name": state["sorts"][sid].get("name", sid),
            "n": len(current.get(sid, [])),
            "signal": sid in config.SIGNAL_SORTS,
            "first": state["sorts"][sid].get("first"),
        }
        for sid in sort_ids
    ]
    ranks = {}
    for sid, ids in current.items():
        if sid in sort_index:
            for rank, uid in enumerate(ids, start=1):
                ranks.setdefault(uid, []).append([sort_index[sid], rank])

    rows, group_rows, stale = [], [], 0
    week_ago = latest - 7 * 86400
    for gid, meta in games.items():
        uid = int(gid)
        s = series.get(uid)
        st = state["games"].get(gid, {})
        if not s:
            stale += 1
            continue
        age = latest - s["t"][-1]
        cold = st.get("lc", 0) < config.HOT_CCU
        if age > (config.STALE_COLD_S if cold else config.STALE_HOT_S):
            stale += 1
            continue
        created = meta.get("c")
        ccu = s["ccu"][-1]
        refs = [_ref(s["t"], s["ccu"], ref_at[w], window_seconds[w], created, available[w])
                for w in range(len(windows))]
        visits = s["visits"][-1] if s["visits"] else None
        favs = s["favs"][-1] if s["favs"] else None
        up = s["up"][-1] if s["up"] else None
        down = s["down"][-1] if s["down"] else None
        d24, d7 = window_seconds[i24], window_seconds[i7d]
        entries = [
            [sort_index[sid], rec[2]]
            for sid, rec in (st.get("s") or {}).items()
            if sid in sort_index and rec[2] and rec[2] >= week_ago
        ]
        i0 = 0
        while i0 < len(s["t"]) and s["t"][i0] < week_ago:
            i0 += 1
        peak7d = max(s["ccu"][i0:]) if i0 < len(s["t"]) else ccu
        caught = st.get("e") or [None, None, None, None]
        rows.append([
            uid, meta.get("n", ""), meta.get("p"), meta.get("g1", ""), meta.get("g2", ""),
            meta.get("cr", ""), created, meta.get("u"), meta.get("fs"),
            ccu, age,
            *refs,
            visits,
            _ref(s["vt"], s["visits"], ref_at[i24], d24, created, available[i24]),
            _ref(s["vt"], s["visits"], ref_at[i7d], d7, created, available[i7d]),
            up, down,
            _ref(s["ut"], s["up"], ref_at[i24], d24, created, available[i24]),
            _ref(s["ut"], s["down"], ref_at[i24], d24, created, available[i24]),
            favs,
            _ref(s["ft"], s["favs"], ref_at[i24], d24, created, available[i24]),
            peak7d, (st.get("pk") or [ccu])[0],
            sorted(ranks.get(uid, [])), sorted(entries),
            meta.get("src", ""), *caught,
        ])
        group_rows.append({
            "ccu": ccu,
            "refs": refs,
            "new": created is not None and latest - created < config.NEW_GAME_DAYS * 86400,
            "entries": [ts for idx, ts in entries if sort_ids[idx] in config.SIGNAL_SORTS],
            "g1": meta.get("g1", ""),
            "g2": meta.get("g2", ""),
        })
    order = sorted(range(len(rows)), key=lambda i: (-rows[i][9], rows[i][0]))
    rows = [rows[i] for i in order]
    group_rows = [group_rows[i] for i in order]

    market, by_l1 = group_stats(group_rows, lambda r: [r["g1"]], window_seconds, latest)
    _, by_l2 = group_stats(
        group_rows, lambda r: [f"{r['g1']} / {r['g2']}"] if r["g2"] else [], window_seconds, latest
    )

    return {
        "v": 1,
        "generated": generated,
        "latest": latest,
        "first": first_ever,
        "windows": [name for name, _ in windows],
        "windowSeconds": window_seconds,
        "available": available,
        "newGameDays": config.NEW_GAME_DAYS,
        "health": health_block(status, snaps, latest, first_ever),
        "totals": {
            "games": len(rows),
            "tracked": len(games),
            "stale": stale,
            "ccu": market["ccu"],
            "new": sum(1 for r in group_rows if r["new"]),
            "newCcu": sum(r["ccu"] for r in group_rows if r["new"]),
        },
        "market": market,
        "early": early_block(status, rows, latest),
        "sorts": sorts_out,
        "cols": GAME_COLS,
        "games": rows,
        "genres": {"l1": by_l1, "l2": by_l2},
        "series": build_series(store, series, rows, latest),
    }


def build_series(store, series, rows, latest):
    """Bucketed player counts: hourly (48h), 3-hourly (7d) and daily averages."""
    keep = [r[0] for r in rows if r[27] >= config.SERIES_MIN_PEAK][: config.SERIES_MAX_GAMES]
    keep_set = set(keep)
    out = {}
    for key, step, points in (
        ("h", 3600, config.SERIES_HOURLY_POINTS),
        ("h3", 3 * 3600, config.SERIES_3H_POINTS),
    ):
        edges = [latest - step * (points - k) for k in range(points + 1)]
        total = [0.0] * points
        seen = [False] * points
        per_game = {}
        for uid, s in series.items():
            means, _ = bucket_stats(s["t"], s["ccu"], edges, config.LONG_GAP_S)
            for k, value in enumerate(means):
                if value is not None:
                    total[k] += value
                    seen[k] = True
            if uid in keep_set:
                per_game[str(uid)] = [None if v is None else round(v) for v in means]
        out[key] = {
            "end": [edges[k + 1] for k in range(points)],
            "step": step,
            "total": [round(total[k]) if seen[k] else None for k in range(points)],
            "g": per_game,
        }

    days = store.daily_days()[-config.SERIES_DAILY_POINTS:]
    daily_total, per_game = [], {str(uid): [] for uid in keep}
    for day in days:
        data = store.load_daily(day) or {"g": {}, "total": None, "span": [0, 0]}
        span_h = (data["span"][1] - data["span"][0]) / 3600 if data.get("span") else 0
        usable = span_h >= config.MIN_DAY_COVERAGE_H
        daily_total.append(data.get("total") if usable else None)
        for gid, values in per_game.items():
            entry = data["g"].get(gid)
            ok = usable and entry is not None and entry[2] >= config.MIN_DAY_COVERAGE_H
            values.append(entry[0] if ok else None)
    out["d"] = {"days": days, "total": daily_total, "g": per_game}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="data directory")
    args = parser.parse_args(argv)
    store = Store(args.data)
    summary = build_summary(store)
    store.write("summary.json", summary)
    if summary.get("empty"):
        print("summary: no snapshots yet")
    else:
        print(
            f"summary: {summary['totals']['games']} games, {summary['totals']['ccu']} players, "
            f"{summary['early']['now']} early catches ({summary['early']['scouted']} games from the scout), "
            f"windows available: {[n for n, a in zip(summary['windows'], summary['available']) if a]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
