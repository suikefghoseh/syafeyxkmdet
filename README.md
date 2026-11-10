# syafeyxkmdet

Collects public Roblox game statistics on a schedule.

- `collector/` reads the public explore sorts and game stats (players, visits,
  votes, favourites, dates) and writes snapshots.
- A GitHub Actions workflow runs it every 30 minutes and commits the results to
  the `data` branch.
- `summary.json` on the `data` branch holds precomputed per-game numbers
  (current players, change over 1h/3h/6h/12h/24h/48h/7d, sort positions).

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
