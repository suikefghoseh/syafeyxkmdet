# syafeyxkmdet

Collects public Roblox game statistics on a schedule.

- `collector/` reads the public explore sorts and game stats (players, visits,
  votes, favourites, dates) and writes snapshots.
- A GitHub Actions workflow runs it every 30 minutes and commits the results to
  the `data` branch.
- `summary.json` on the `data` branch holds precomputed per-game numbers
  (current players, change over 1h/3h/6h/12h/24h/48h/7d, sort positions,
  early catches).

## Early catches

A game is an early catch while it has many players for how few visits it has
had in total: at least `EARLY_MIN_CCU` players and `EARLY_MIN_RATIO` players
per lifetime visit (50 players and 1 per 1,000 visits). The game's age plays no
part, so an old game that suddenly takes off is caught like a new one. The
first time a tracked game meets the rule, the collector records when it
happened and the players and visits at that moment (`earlyAt`, `earlyCcu`,
`earlyVisits` in the summary), so a catch can be followed afterwards.

The explore charts only list games that are already big, so every run also
has a scout (`collector/scout.py`). It reads keyword searches and the
Up-and-Coming and Top Trending charts of single countries, which list small
games too, and starts tracking any game that meets the rule (`src` in the
summary says which listing found it). Games that do not meet it are remembered
in `scout.json` with their visit count and are only asked about again once
their players could carry them over the bar. The scout runs after the regular
poll with its own time budget, and nothing it does can fail a run. A scouted
game that never reaches a chart and stays under the player minimum for
`SCOUT_DROP_DAYS` is dropped again.

Python standard library only. No keys or accounts are needed.

```bash
python3 -m unittest discover -s tests -t .
```

```bash
python3 -m collector.collect --data ./data
```

```bash
python3 -m collector.rollup --data ./data
```

```bash
python3 -m collector.summarize --data ./data
```
