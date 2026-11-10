"""The early-catch rule: many players for how few visits a game has had.

A game's age plays no part. A game that jumps to 500 players on a few
thousand lifetime visits is an early catch whether it was made yesterday or
two years ago, and a ten-day-old game with millions of visits is not one.
"""

from . import config


def ratio(ccu, visits):
    """Players now per lifetime visit, between 0 and 1."""
    if not ccu or ccu <= 0:
        return 0.0
    # The visit counter can lag behind a sudden spike, but everyone inside
    # right now has visited at least once.
    return ccu / max(visits or 0, ccu)


def is_early(ccu, visits):
    return ccu is not None and ccu >= config.EARLY_MIN_CCU and ratio(ccu, visits) >= config.EARLY_MIN_RATIO
