# Running WhaleSweep on the VPS

The full 10-year, multi-asset (forex + indices + stocks) parameter
search is meant to run on the VPS, not this laptop — same reasoning as
`../MeanReversion/VPS_DEPLOYMENT.md`: it's faster hardware, and this
laptop's local Dukascopy cache doesn't have the stock CFDs (AAPL/WMT/
XOM/WBD) that the VPS's cache does. This laptop was used only to build
and validate the pipeline (see README.md's "Status" section) on a 3-year
forex + index subset.

## First time setup

```powershell
git clone <this repo's remote>   # once pushed — see "Sending results back" below
cd WhaleSweep
py -3 -m pip install --quiet numpy pandas pyarrow

# Point at the VPS's Dukascopy cache (same env var convention as
# MeanReversion's MR_DUKASCOPY_ROOT). Confirmed 2026-09-19 against Tim's
# own directory listing:
$env:WS_DUKASCOPY_ROOT = "C:\Users\Administrator\RCTBE\data\dukascopy"

.\precompute_all.ps1        # precomputes all 18 assets x 3 timeframes
py -3 selftest.py           # cheap plumbing check — run this before trusting a long search
```

**Stock codes**: `precompute.py`'s `STOCK_ASSETS` dict
(`AAPLUSUSD`/`WMTUSUSD`/`XOMUSUSD`/`DISUSUSD`) matches the VPS cache's
real folder names, confirmed against Tim's own directory listing
2026-09-19. One substitution worth knowing about: Tim asked for WBD
(Discovery), but that listing has no WBD folder — DIS (Disney) is used
in its place. If a WBD folder ever shows up in the cache, swap it back
in (README.md flags this too). `precompute_all.ps1` will warn (not
crash) on any asset it can't find.

## Running the search

```powershell
.\start_search_4x.ps1                        # 4-way parallel, 100k-iteration ceiling per process
.\start_search_4x.ps1 -Groups 6 -Iterations 50000
```

Splits the 18-asset universe round-robin across `$Groups` processes,
each with its own `WS_ASSETS`/`WS_OUTPUT_DIR` so they never collide on
the same checkpoint/results files, at `BelowNormal` priority. Safe to
disconnect the RDP session afterward (not log off — only a log-off or
reboot kills the processes, same as MeanReversion's own launcher).

Check progress:
```powershell
Get-Content whale_sweep_output_group0\run.log -Tail 20 -Wait
```

To stop everything (hard kill — loses up to the last checkpoint,
`WS_CHECKPOINT_EVERY` iterations, default 200):
```powershell
Get-Process -Name py -ErrorAction SilentlyContinue | Stop-Process
```

### Minimal path (single process, foreground)

```powershell
$env:WS_ITERATIONS = "200000"
py -3 whale_sweep.py
```

## After the search — vetting a candidate before trusting it

A high `score` in `top_strategies.json` is a search-time filter, not a
verdict — run every serious candidate through all three gates before
sizing any real risk against it (same discipline MeanReversion applies
via its own `gate2_holdout.py`/`plateau_check.py`/`candidate_report.py`):

```powershell
py -3 candidate_report.py --asset NDX100 --tf 5min --rank 0 1 2
```

This runs, in order: the OOS-holdout re-check (`gate2_holdout.py`), the
parameter-plateau check (`plateau_check.py`), and a real-historical-
replay FTMO check (every-Monday cohort replay through
`ftmo_challenge_rules.py`, reporting both the overall pass rate and the
rolling-worst-24-month-window pass rate — trust the worst-window number,
not the average, per that file's own docstring).

## Sending results back

```powershell
git add ws_precomputed_*.parquet whale_sweep_output_group*/top_strategies.json
git commit -m "WhaleSweep search results"
git push
```

Same "only the precomputed data + each group's `top_strategies.json` are
worth versioning" convention as MeanReversion — everything else under
`whale_sweep_output_group*/` is gitignored as regenerable working state.
