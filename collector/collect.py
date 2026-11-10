"""One collection run: read the explore sorts, then poll every tracked game.

Usage: python -m collector.collect --data DIR

Exit code 0 means a snapshot was saved. Exit code 3 means the run failed: no
snapshot is written (never an empty or mostly-empty one) and the failure is
recorded in status.json.
"""

import argparse
import re
import sys
import time
import uuid

from . import config
from .client import Client, DeadlineExceeded, HttpError
from .store import SNAPSHOT_COLS, Store

_TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")


class RunFailed(Exception):
    """The run produced nothing trustworthy."""


def parse_time(value):
    """'2024-07-28T02:20:42.22Z' -> unix seconds (UTC), or None."""
    import calendar

    match = _TIME_RE.match(value or "")
    if not match:
        return None
    return calendar.timegm(tuple(int(x) for x in match.groups()) + (0, 0, 0))


def clean_description(text):
    text = " ".join((text or "").split())
    if len(text) > config.DESCRIPTION_MAX_CHARS:
        text = text[: config.DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
    return text


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------
# Sorts
# --------------------------------------------------------------------------
def _sort_games(sort):
    """Universe ids of a sort in rank order (sponsored tiles skipped), plus seed info."""
    ids, seed = [], {}
    for game in sort.get("games") or []:
        uid = game.get("universeId")
        if not isinstance(uid, int) or game.get("isSponsored") or uid in seed:
            continue
        ids.append(uid)
        seed[uid] = {"n": game.get("name") or "", "p": game.get("rootPlaceId")}
    return ids, seed


def fetch_sorts(client, log=print):
    """Return ({sortId: {"name", "ids"}}, {universeId: seed info})."""
    session = str(uuid.uuid4())
    sorts, seeds = {}, {}
    token, pages = None, 0
    while pages < 20:
        params = {"sessionId": session, **config.SORT_FILTERS}
        if token:
            params["sortsPageToken"] = token
        try:
            page = client.get_json(config.EXPLORE_API + "/get-sorts", params)
        except (HttpError, DeadlineExceeded) as error:
            if pages == 0:
                raise RunFailed(f"could not read the explore sorts: {error}")
            log(f"  sorts page {pages + 1} failed, continuing with what we have: {error}")
            break
        pages += 1
        for sort in page.get("sorts") or []:
            if sort.get("contentType") != "Games" or not sort.get("sortId"):
                continue
            ids, seed = _sort_games(sort)
            if ids:
                sorts[sort["sortId"]] = {"name": sort.get("sortDisplayName") or sort["sortId"], "ids": ids}
                seeds.update(seed)
        token = page.get("nextSortsPageToken")
        if not token:
            break

    core = max((len(sorts.get(s, {}).get("ids", [])) for s in config.CORE_SORTS), default=0)
    if core < config.MIN_CORE_SORT_GAMES:
        raise RunFailed(f"explore sorts came back empty (largest core sort has {core} games)")

    for sort_id, device in config.DISCOVERY_VARIANTS:
        params = {"sessionId": session, "sortId": sort_id, **config.SORT_FILTERS, "device": device}
        try:
            sort = client.get_json(config.EXPLORE_API + "/get-sort-content", params)
        except (HttpError, DeadlineExceeded) as error:
            log(f"  variant {sort_id}@{device} skipped: {error}")
            continue
        ids, seed = _sort_games(sort)
        if ids:
            name = (sort.get("sortDisplayName") or sort_id) + f" ({device})"
            sorts[f"{sort_id}@{device}"] = {"name": name, "ids": ids}
            seeds.update(seed)
    return sorts, seeds


# --------------------------------------------------------------------------
# Which games to poll
# --------------------------------------------------------------------------
def is_hot(meta, st, now):
    if now - st.get("ls", 0) <= config.HOT_SORT_DAYS * 86400:
        return True
    if st.get("lc", 0) >= config.HOT_CCU:
        return True
    if now - meta.get("fs", now) <= config.HOT_FIRST_SEEN_DAYS * 86400:
        return True
    created = meta.get("c")
    return created is not None and now - created <= config.HOT_NEW_DAYS * 86400


def plan_polls(games, gstate, now):
    """(hot ids, due cold ids). Hot games are ordered by last known players."""
    hot, cold = [], []
    for gid, st in gstate.items():
        meta = games.get(gid, {})
        if st.get("dead"):
            if now - st.get("lp", 0) >= config.DEAD_INTERVAL_S:
                cold.append(gid)
        elif is_hot(meta, st, now):
            hot.append(gid)
        elif now - st.get("lp", 0) >= config.COLD_INTERVAL_S:
            cold.append(gid)
    hot.sort(key=lambda g: (-gstate[g].get("lc", 0), int(g)))
    cold.sort(key=lambda g: (gstate[g].get("lp", 0), int(g)))
    return hot, cold


# --------------------------------------------------------------------------
# Game stats
# --------------------------------------------------------------------------
def fetch_stats(client, ids, log=print):
    """Return ({id: details dict}, {id: (up, down)}, ids whose details call failed)."""
    details, votes, failed = {}, {}, set()
    stopped = False
    for batch in chunks(ids, config.DETAILS_BATCH):
        if stopped:
            failed.update(batch)
            continue
        try:
            data = client.get_json(config.GAMES_API + "/games", {"universeIds": ",".join(batch)})
        except DeadlineExceeded as error:
            log(f"  stopping early: {error}")
            stopped = True
            failed.update(batch)
            continue
        except HttpError as error:
            log(f"  details batch failed: {error}")
            failed.update(batch)
            continue
        for item in data.get("data") or []:
            if isinstance(item.get("id"), int) and isinstance(item.get("playing"), int):
                details[str(item["id"])] = item

    for batch in chunks([g for g in ids if g in details], config.VOTES_BATCH):
        if stopped:
            break
        try:
            data = client.get_json(config.GAMES_API + "/games/votes", {"universeIds": ",".join(batch)})
        except DeadlineExceeded as error:
            log(f"  stopping early: {error}")
            stopped = True
            continue
        except HttpError as error:
            log(f"  votes batch failed: {error}")
            continue
        for item in data.get("data") or []:
            if isinstance(item.get("id"), int):
                votes[str(item["id"])] = (item.get("upVotes"), item.get("downVotes"))
    return details, votes, failed


def apply_details(meta, item):
    creator = item.get("creator") or {}
    meta["n"] = item.get("name") or meta.get("n") or ""
    meta["d"] = clean_description(item.get("description"))
    meta["p"] = item.get("rootPlaceId") or meta.get("p")
    meta["c"] = parse_time(item.get("created"))
    meta["u"] = parse_time(item.get("updated"))
    meta["g1"] = item.get("genre_l1") or ""
    meta["g2"] = item.get("genre_l2") or ""
    meta["cr"] = creator.get("name") or ""
    meta["ct"] = "G" if creator.get("type") == "Group" else "U"
    meta["mp"] = item.get("maxPlayers")


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def collect_once(store, client, now=time.time, log=print):
    """Do one run. Returns a small result dict; raises RunFailed on failure."""
    games = store.load_games()
    state = store.load_state()
    gstate = state["games"]

    started = int(now())
    sorts, seeds = fetch_sorts(client, log)
    log(f"sorts: {len(sorts)} with {len(seeds)} distinct games")

    # Register newly seen games so they are polled in this same run.
    new_ids = []
    for uid, seed in seeds.items():
        gid = str(uid)
        if gid not in games:
            games[gid] = {"n": seed["n"], "p": seed["p"], "fs": started}
            new_ids.append(gid)
        st = gstate.setdefault(gid, {})
        st["ls"] = started
        st.pop("dead", None)

    hot, cold = plan_polls(games, gstate, started)
    wanted = hot + cold
    log(f"polling {len(hot)} hot + {len(cold)} due cold games ({len(new_ids)} new)")

    t0 = int(now())
    details, votes, failed = fetch_stats(client, wanted, log)
    t1 = int(now())
    snapshot_time = (t0 + t1) // 2

    hot_asked = len(hot)
    failed_hot = sum(1 for g in hot if g in failed)
    if hot_asked and (hot_asked - failed_hot) / hot_asked < config.MIN_STATS_COVERAGE:
        raise RunFailed(f"stats came back for only {hot_asked - failed_hot} of {hot_asked} games")
    if not details or sum(item["playing"] for item in details.values()) <= 0:
        raise RunFailed("stats response was empty")

    # Snapshot rows.
    rows = []
    for gid in wanted:
        item = details.get(gid)
        if item is None:
            continue
        up, down = votes.get(gid, (None, None))
        rows.append([int(gid), item["playing"], item.get("visits"), up, down, item.get("favoritedCount")])

    # Registry and per-game bookkeeping.
    for gid in wanted:
        st = gstate[gid]
        item = details.get(gid)
        if item is None:
            if gid not in failed:  # the API answered but left this game out
                st["miss"] = st.get("miss", 0) + 1
                st["lp"] = snapshot_time
                if st["miss"] >= config.DEAD_AFTER_MISSES:
                    st["dead"] = snapshot_time
            continue
        apply_details(games[gid], item)
        st["lp"] = snapshot_time
        st["lc"] = item["playing"]
        st.pop("miss", None)
        st.pop("dead", None)
        peak = st.get("pk")
        if not peak or item["playing"] > peak[0]:
            st["pk"] = [item["playing"], snapshot_time]

    # Sort membership: [first seen, last seen, last entry, best rank].
    # An entry is a sighting after REENTRY_GAP_S of absence. The first time a
    # sort itself is observed nothing counts as an entry (there is no "before").
    for sort_id, sort in sorts.items():
        known = state["sorts"].get(sort_id)
        sort_is_new = known is None
        state["sorts"][sort_id] = {
            "name": sort["name"],
            "first": snapshot_time if sort_is_new else known["first"],
            "last": snapshot_time,
            "n": len(sort["ids"]),
        }
        for rank, uid in enumerate(sort["ids"], start=1):
            membership = gstate[str(uid)].setdefault("s", {})
            record = membership.get(sort_id)
            if record is None:
                membership[sort_id] = [snapshot_time, snapshot_time, 0 if sort_is_new else snapshot_time, rank]
            else:
                if snapshot_time - record[1] > config.REENTRY_GAP_S:
                    record[2] = snapshot_time
                record[1] = snapshot_time
                record[3] = min(record[3], rank)

    partial = bool(failed)  # some batches failed or the deadline cut the run short
    snapshot = {
        "v": 1,
        "t": snapshot_time,
        "t0": t0,
        "t1": t1,
        "partial": partial,
        "cols": SNAPSHOT_COLS,
        "g": rows,
        "s": {sort_id: sort["ids"] for sort_id, sort in sorts.items()},
    }
    if state["first"] is None:
        state["first"] = snapshot_time
    name = store.write_snapshot(snapshot)
    store.save_games(games)
    store.save_state(state)
    log(f"saved {name}: {len(rows)} games, partial={partial}")
    return {
        "t": snapshot_time,
        "games": len(rows),
        "new": len(new_ids),
        "partial": partial,
        "failed": len(failed),
        "ccu": sum(row[1] for row in rows),
    }


def record_run(store, entry, error=None):
    status = store.load_status()
    status["lastAttempt"] = entry["at"]
    status["ok"] = error is None
    if error is None:
        status["lastOk"] = entry["t"]
        status["failStreak"] = 0
        status["lastError"] = None
    else:
        status["failStreak"] = status.get("failStreak", 0) + 1
        status["lastError"] = {"at": entry["at"], "message": str(error)[:300]}
    status["runs"] = (status.get("runs") or [])[-(config.STATUS_HISTORY - 1) :] + [entry]
    store.write("status.json", status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="data directory")
    args = parser.parse_args(argv)

    store = Store(args.data)
    client = Client(deadline_s=config.RUN_DEADLINE_S)
    began = time.time()
    error, result = None, {}
    try:
        result = collect_once(store, client)
    except RunFailed as failure:
        error = failure
    except Exception as failure:  # keep the status file truthful on unexpected errors too
        error = RunFailed(f"{type(failure).__name__}: {failure}")

    entry = {
        "at": int(began),
        "ok": error is None,
        "t": result.get("t"),
        "games": result.get("games", 0),
        "new": result.get("new", 0),
        "partial": bool(result.get("partial")),
        "requests": client.requests,
        "retries": client.retries,
        "r429": client.rate_limited,
        "secs": int(time.time() - began),
    }
    record_run(store, entry, error)
    print(
        f"requests={client.requests} retries={client.retries} 429s={client.rate_limited} "
        f"seconds={entry['secs']}"
    )
    if error is not None:
        print(f"RUN FAILED: {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
