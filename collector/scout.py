"""Scout: find games that are in no main chart yet.

The explore charts list games that are already big. To catch a game while it
is still small, the scout reads listings that show small games too: keyword
searches and the discovery charts of single countries. Every listing gives a
player count per game, so most games are ruled out without asking for their
stats. A game that meets the early rule is handed back to be tracked like any
other; one that does not is remembered in scout.json with its visit count and
is only asked about again once its players could carry it over the bar.
"""

import re
import uuid

from . import config
from .client import DeadlineExceeded, HttpError
from .early import is_early

_WORD_RE = re.compile(r"[a-z][a-z0-9']{2,}")
# Title words that say nothing about what a game is.
_FILLER = {
    "the", "and", "for", "your", "you", "with", "from", "into", "that", "this", "but",
    "not", "all", "get", "got", "more", "now", "out", "are", "its", "our", "has",
    "free", "codes", "code", "event", "part", "world", "game", "games", "roblox",
    "big", "best", "super", "mega", "ultra", "pro", "max", "day", "days", "week",
    "x2", "x10", "x100", "x1000",
}


def rotation():
    """Every listing read in turn, a slice per run."""
    charts = [("chart", sort_id, country) for country in config.SCOUT_COUNTRIES for sort_id in config.SCOUT_SORTS]
    return charts + [("search", word) for word in config.SCOUT_KEYWORDS]


def label(query):
    """How a find is credited, for example 'search:release' or 'chart:up-and-coming@ph'."""
    return f"search:{query[1]}" if query[0] == "search" else f"chart:{query[1]}@{query[2]}"


def trend_words(names, limit=None):
    """Words shared by the titles of several current catches, most common first.

    A new craze shows up as a word that suddenly sits in many small games'
    titles, so searching for it finds the next ones early.
    """
    limit = config.SCOUT_TREND_WORDS if limit is None else limit
    known = {w for phrase in config.SCOUT_PINNED + config.SCOUT_KEYWORDS for w in phrase.split()}
    counts = {}
    for name in names:
        for word in set(_WORD_RE.findall((name or "").lower())):
            if word not in known and word not in _FILLER:
                counts[word] = counts.get(word, 0) + 1
    shared = [w for w, n in counts.items() if n >= 2]
    shared.sort(key=lambda w: (-counts[w], w))
    return shared[:limit]


def plan(cursor, names):
    """(queries for this run, how many of them come from the rotation)."""
    fixed = [("search", word) for word in config.SCOUT_PINNED + trend_words(names)]
    fixed = fixed[: config.SCOUT_QUERIES_PER_RUN]
    turn = rotation()
    room = min(len(turn), config.SCOUT_QUERIES_PER_RUN - len(fixed))
    return fixed + [turn[(cursor + i) % len(turn)] for i in range(room)], room


def read_listing(client, query, session):
    """One listing as [(universe id, players, votes)], sponsored tiles left out."""
    if query[0] == "search":
        data = client.get_json(
            config.SEARCH_API,
            {"searchQuery": query[1], "pageToken": "", "sessionId": session, "pageType": "all"},
        )
        tiles = [
            tile
            for group in data.get("searchResults") or []
            if group.get("contentGroupType") == "Game"
            for tile in group.get("contents") or []
        ]
    else:
        data = client.get_json(
            config.EXPLORE_API + "/get-sort-content",
            {"sessionId": session, "sortId": query[1], **config.SORT_FILTERS, "country": query[2]},
        )
        tiles = data.get("games") or []
    out = []
    for tile in tiles:
        uid = tile.get("universeId")
        if isinstance(uid, int) and not tile.get("isSponsored"):
            votes = (tile.get("totalUpVotes") or 0) + (tile.get("totalDownVotes") or 0)
            out.append((uid, tile.get("playerCount") or 0, votes))
    return out


def run(client, games, gstate, cache, now, log=print):
    """Look for untracked games that meet the early rule.

    Returns ({id: (details item, how it was found)}, counters for the run log).
    Nothing here can fail the run: on errors, rate limits or a spent time
    budget the scout stops and returns what it has.
    """
    began = now()
    stamp = int(began)
    rate_limited_before = client.rate_limited
    seen = cache["seen"]
    session = str(uuid.uuid4())
    current = [
        games[gid].get("n", "")
        for gid, st in gstate.items()
        if gid in games and st.get("e") and st["e"][3] == st.get("lp")
    ]
    cursor = cache.get("cursor", 0)
    queries, from_rotation = plan(cursor, current)
    fixed = len(queries) - from_rotation

    candidates, listed, done = {}, set(), 0
    for query in queries:
        if now() - began > config.SCOUT_BUDGET_S:
            log(f"  scout: time budget used after {done} listings")
            break
        if client.rate_limited - rate_limited_before >= config.SCOUT_MAX_429:
            log(f"  scout: rate limited, stopping after {done} listings")
            break
        try:
            tiles = read_listing(client, query, session)
        except DeadlineExceeded as error:
            log(f"  scout: stopping early: {error}")
            break
        except HttpError as error:
            log(f"  scout: {label(query)} skipped: {error}")
            tiles = []
        done += 1
        for uid, players, votes in tiles:
            gid = str(uid)
            listed.add(gid)
            if gid in games:
                continue
            known = seen.get(gid)
            if known is not None:
                known[1] = stamp
            # Visits only ever grow, so a game that would not pass with the
            # visits it had at the last check cannot pass now either.
            if not is_early(players, known[0] if known else 0):
                continue
            best = candidates.get(gid)
            if best is None or players > best[0]:
                candidates[gid] = (players, votes, label(query))
    cache["cursor"] = (cursor + max(0, done - fixed)) % len(rotation())

    # Most players per vote first: few votes for the crowd means few visits so far.
    order = sorted(candidates, key=lambda g: (-candidates[g][0] / (candidates[g][1] + 1), int(g)))
    order = order[: config.SCOUT_MAX_CHECKS]
    found, checked = {}, 0
    for start in range(0, len(order), config.DETAILS_BATCH):
        batch = order[start : start + config.DETAILS_BATCH]
        try:
            data = client.get_json(config.GAMES_API + "/games", {"universeIds": ",".join(batch)})
        except DeadlineExceeded as error:
            log(f"  scout: stopping early: {error}")
            break
        except HttpError as error:
            log(f"  scout: details batch failed: {error}")
            continue
        for item in data.get("data") or []:
            if not isinstance(item.get("id"), int) or not isinstance(item.get("playing"), int):
                continue
            gid = str(item["id"])
            if gid not in candidates:
                continue
            checked += 1
            if is_early(item["playing"], item.get("visits")):
                found[gid] = (item, candidates[gid][2])
                seen.pop(gid, None)
            else:
                seen[gid] = [item.get("visits") or 0, stamp]

    cutoff = stamp - config.SCOUT_FORGET_DAYS * 86400
    for gid in [g for g, record in seen.items() if record[1] < cutoff]:
        del seen[gid]
    return found, {"q": done, "listed": len(listed), "checked": checked, "added": len(found)}
