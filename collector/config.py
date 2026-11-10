"""Settings for the collector. Everything tunable lives here."""

EXPLORE_API = "https://apis.roblox.com/explore-api/v1"
GAMES_API = "https://games.roblox.com/v1"
USER_AGENT = "Mozilla/5.0 (compatible; rbx-stats-collector/1.0)"

# --- Request pacing -------------------------------------------------------
MIN_REQUEST_INTERVAL_S = 2.0   # at most one request every 2 seconds
MAX_ATTEMPTS = 5               # per request, including the first try
MAX_RETRY_WAIT_S = 300         # upper bound for a single Retry-After wait
RUN_DEADLINE_S = 18 * 60       # stop fetching after this long; the run is then partial

# --- Batch sizes (hard limits of the endpoints) ---------------------------
DETAILS_BATCH = 50             # games.roblox.com/v1/games rejects more than 50 ids
VOTES_BATCH = 100              # games.roblox.com/v1/games/votes accepts 100 ids

# --- Which charts to read -------------------------------------------------
# The explore page is read as "all devices, all countries".
SORT_FILTERS = {"device": "all", "country": "all"}

# Extra device views of the discovery sorts. They only help to find games
# earlier and are stored as "<sortId>@<device>".
DISCOVERY_VARIANTS = [
    ("top-trending", "computer"),
    ("top-trending", "high_end_phone"),
    ("top-trending", "console"),
    ("up-and-coming", "computer"),
    ("up-and-coming", "high_end_phone"),
    ("up-and-coming", "console"),
]

# Sorts whose entries count as a signal in the summary.
SIGNAL_SORTS = [
    "top-trending",
    "up-and-coming",
    "top-playing-now",
    "most-popular",
    "top-revisited",
    "top-earning",
    "top-rated",
]

# A run only counts if at least one of these came back with real content.
CORE_SORTS = ["top-playing-now", "top-trending", "up-and-coming"]
MIN_CORE_SORT_GAMES = 20
MIN_STATS_COVERAGE = 0.5       # below this share of requested games the run fails

# --- Which games are polled every run -------------------------------------
HOT_CCU = 100                  # players at the last poll
HOT_SORT_DAYS = 7              # appeared in any sort this recently
HOT_NEW_DAYS = 45              # created this recently
HOT_FIRST_SEEN_DAYS = 3        # first seen by the collector this recently
COLD_INTERVAL_S = 6 * 3600     # everything else is polled this often
DEAD_INTERVAL_S = 24 * 3600    # games the API stopped returning
DEAD_AFTER_MISSES = 6
REENTRY_GAP_S = 12 * 3600      # absent this long from a sort => next sighting is an entry

# --- Stored text ----------------------------------------------------------
DESCRIPTION_MAX_CHARS = 400

# --- Summary --------------------------------------------------------------
WINDOWS = [
    ("1h", 3600),
    ("3h", 3 * 3600),
    ("6h", 6 * 3600),
    ("12h", 12 * 3600),
    ("24h", 24 * 3600),
    ("48h", 48 * 3600),
    ("7d", 7 * 86400),
]
NEW_GAME_DAYS = 30             # "new" = created less than this many days ago
SUMMARY_LOOKBACK_S = int(8.5 * 86400)   # raw history read for the summary
STALE_HOT_S = 2 * 3600         # a game's latest value is unknown if older than this
STALE_COLD_S = 9 * 3600
SHORT_GAP_S = 2 * 3600         # largest gap bridged by interpolation, windows <= 3h
LONG_GAP_S = 8 * 3600          # largest gap bridged by interpolation, longer windows
SERIES_HOURLY_POINTS = 48      # 48 hourly buckets ending at the latest snapshot
SERIES_3H_POINTS = 56          # 7 days of 3-hour buckets
SERIES_DAILY_POINTS = 60       # up to 60 daily averages from the rollups
SERIES_MIN_PEAK = 100          # games below this 7-day peak get no series
SERIES_MAX_GAMES = 2000

# --- Retention ------------------------------------------------------------
RAW_RETENTION_DAYS = 14        # raw snapshots older than this exist only as daily rollups
MIN_DAY_COVERAGE_H = 12        # a daily average needs at least this much coverage
STATUS_HISTORY = 400           # run log entries kept in status.json (~8 days)
