"""Aggregate per-game numbers into groups (for example Roblox's own genres).

A game can belong to several groups. Changes are like-for-like: for each
window only games with a known value at both ends are compared, so a game
that was simply not tracked yet cannot fake growth. A game that did not exist
at the start of a window has a true starting value of 0 and is included.
"""


def pct_change(now, ref):
    if now is None or ref is None or ref <= 0:
        return None
    return (now - ref) / ref


def market_stats(rows, n_windows):
    """Totals over all rows: current players and matched change per window."""
    total = sum(r["ccu"] for r in rows if r["ccu"] is not None)
    now_w, ref_w = [0.0] * n_windows, [0.0] * n_windows
    for r in rows:
        if r["ccu"] is None:
            continue
        for w in range(n_windows):
            ref = r["refs"][w]
            if ref is not None:
                now_w[w] += r["ccu"]
                ref_w[w] += ref
    return {
        "ccu": total,
        "now": [round(v) for v in now_w],
        "ref": [round(v) for v in ref_w],
        "chg": [_round(pct_change(now_w[w], ref_w[w])) for w in range(n_windows)],
    }


def group_stats(rows, labels_of, window_seconds, latest):
    """Per-group stats.

    rows: dicts with ccu (int or None), refs (one value or None per window),
    new (bool) and entries (timestamps of the game's latest entry into each
    signal sort). labels_of(row) returns the groups the row belongs to.
    """
    n_windows = len(window_seconds)
    market = market_stats(rows, n_windows)
    groups = {}
    for r in rows:
        if r["ccu"] is None:
            continue
        for label in labels_of(r):
            if not label:
                continue
            g = groups.get(label)
            if g is None:
                g = groups[label] = {
                    "games": 0, "ccu": 0, "top": 0, "n100": 0, "new": 0, "newCcu": 0,
                    "now": [0.0] * n_windows, "ref": [0.0] * n_windows,
                    "matched": [0] * n_windows, "ent": [0] * n_windows,
                }
            g["games"] += 1
            g["ccu"] += r["ccu"]
            g["top"] = max(g["top"], r["ccu"])
            if r["ccu"] >= 100:
                g["n100"] += 1
            if r["new"]:
                g["new"] += 1
                g["newCcu"] += r["ccu"]
            for w in range(n_windows):
                ref = r["refs"][w]
                if ref is not None:
                    g["now"][w] += r["ccu"]
                    g["ref"][w] += ref
                    g["matched"][w] += 1
                if any(ts >= latest - window_seconds[w] for ts in r["entries"]):
                    g["ent"][w] += 1

    out = []
    for label, g in groups.items():
        chg, rel = [], []
        for w in range(n_windows):
            change = pct_change(g["now"][w], g["ref"][w])
            chg.append(_round(change))
            m = market["chg"][w]
            rel.append(None if change is None or m is None else _round((1 + change) / (1 + m) - 1))
        out.append({
            "label": label,
            "games": g["games"],
            "ccu": g["ccu"],
            "share": _round(g["ccu"] / market["ccu"]) if market["ccu"] else None,
            "top": _round(g["top"] / g["ccu"]) if g["ccu"] else None,
            "n100": g["n100"],
            "new": g["new"],
            "newCcu": g["newCcu"],
            "chg": chg,
            "rel": rel,
            "matched": g["matched"],
            "ent": g["ent"],
        })
    out.sort(key=lambda g: (-g["ccu"], g["label"]))
    return market, out


def _round(value, digits=4):
    return None if value is None else round(value, digits)
