"""Settings for the collector. Everything tunable lives here."""

EXPLORE_API = "https://apis.roblox.com/explore-api/v1"
GAMES_API = "https://games.roblox.com/v1"
SEARCH_API = "https://apis.roblox.com/search-api/omni-search"
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

# --- Early catches --------------------------------------------------------
# A game is an early catch while it has many players for how few visits it has
# had in total (players now / lifetime visits). Its age plays no part: 500
# players on a few thousand visits is a catch whenever the game was made.
EARLY_MIN_CCU = 50             # below this many players the ratio is noise
EARLY_MIN_RATIO = 0.001        # 1 player per 1,000 lifetime visits
EARLY_HOT_DAYS = 3             # polled every run for this long after it last met the rule

# --- Scout: games that are in no main chart yet ---------------------------
# The charts above only show games that are already big. The scout reads
# keyword searches and per-country discovery charts, which list small games
# too, and registers the ones that meet the early rule.
SCOUT_CHARTS_PER_RUN = 24      # country charts read per run (all of them every second run)
SCOUT_SEARCHES_PER_RUN = 40    # keyword searches per run, when the search lets us
SCOUT_BUDGET_S = 6 * 60        # reading listings stops after this long
SCOUT_MAX_429 = 3              # listings of one kind that may hit a rate limit before that kind is left for the next run
SCOUT_SLOW_GAP_S = 10          # extra wait between searches after the search asked us to slow down
SCOUT_MAX_GAP_S = 60           # the wait doubles on each further rate limit, up to this
SCOUT_MAX_CHECKS = 300         # unknown games whose stats are fetched per run
SCOUT_TREND_WORDS = 12         # searches for words taken from current catches' titles
SCOUT_OFTEN_REPEATS = 5        # times per cycle the pinned and trend words are searched
SCOUT_FORGET_DAYS = 14         # a rejected game is forgotten once nothing lists it this long
SCOUT_DROP_DAYS = 14           # a scouted game under EARLY_MIN_CCU this long, never charted, is dropped

# Searched most often: the words developers put in the title of a fresh game.
SCOUT_PINNED = ["new", "release", "beta", "alpha", "update", "upd", "testing", "demo"]

# Both kinds of listing are read in rotation, a slice per run: the discovery
# charts of the big Roblox countries, and the keyword searches below.
SCOUT_SORTS = ["up-and-coming", "top-trending"]
SCOUT_COUNTRIES = [
    "us", "br", "ph", "id", "mx", "gb", "ru", "de", "fr", "tr", "vn", "th",
    "in", "jp", "kr", "sa", "pl", "ca", "au", "es", "it", "ar", "co", "my",
]
SCOUT_KEYWORDS = [
    # formats
    "simulator", "tycoon", "obby", "tower", "rng", "clicker", "incremental", "idle",
    "tower defense", "battlegrounds", "roleplay", "rp", "story", "horror", "survival",
    "shooter", "fps", "racing", "fighting", "pvp", "puzzle", "sandbox", "ragdoll",
    "parkour", "minigames", "tag", "hide and seek", "rpg", "dungeon", "adventure",
    "open world", "2 player", "together", "difficulty chart",
    # what the player does
    "+1", "steal", "grow", "build", "catch", "dig", "fish", "mine", "cook", "craft",
    "collect", "hatch", "evolution", "evolve", "merge", "fight", "run", "jump", "climb",
    "slide", "fly", "drive", "ride", "throw", "break", "smash", "cut", "clean", "draw",
    "paint", "dress", "make", "sell", "raise", "feed", "defend", "survive", "hunt",
    "find", "escape", "dont", "be a", "become", "spin", "roll", "open", "trade", "race",
    "speed", "only up",
    # what it is about
    "lucky block", "brainrot", "anime", "meme", "egg", "pet", "garden", "farm", "zoo",
    "restaurant", "cafe", "shop", "factory", "hospital", "school", "hotel", "house",
    "city", "island", "prison", "zombie", "monster", "dragon", "dinosaur", "animal",
    "cat", "dog", "car", "bike", "train", "plane", "boat", "rocket", "space", "sword",
    "gun", "ninja", "pirate", "magic", "soccer", "football", "basketball", "boxing",
    "army", "police", "baby", "family", "troll", "tsunami", "disaster", "killer",
]

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
