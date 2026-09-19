# Running WhaleSweep on the VPS

The full 10-year, multi-asset parameter search is meant to run on the
VPS, not this laptop — same reasoning as `../MeanReversion/VPS_DEPLOYMENT.md`:
it's faster hardware, and this laptop's local Dukascopy cache doesn't
have AAPL, which the VPS's cache does. This laptop was used only to
build and validate the pipeline (see README.md's "Status" section).

## Asset universe (narrowed 2026-09-19 per Tim, GER40 added back same day)

Down from an earlier 18-asset universe to 7, then GER40 (DAX) added
back: **EURUSD, GBPUSD, XAUUSD (gold), NDX100, SPX500, US30, GER40
(DAX), AAPL** — 8 total. GER40 was re-added specifically because the
4-way parallel launcher's round-robin split left its 4th group with
only NDX100 — `start_search_4x.ps1`'s `$allAssets` array puts GER40
last on purpose so it lands in that same group (see that file's own
comment). `precompute.py` and `precompute_all.ps1` all reflect this
8-asset list. If the VPS still has precomputed files or Dukascopy cache
entries for the other dropped assets (USDJPY, AUDUSD, NZDUSD, USDCAD,
USDCHF, FRA40, UK100, JPN225, WMT, XOM, DIS), they're simply ignored
now — harmless to leave in place, but see "Freeing disk space" below
if space is tight.

GER40 (folder `DEUIDXEUR` in the Dukascopy cache) actually already
precomputed successfully on the VPS for 1min/3min before the original
disk-full incident — only 5min failed. Re-running `precompute_all.ps1`
will simply redo all three timeframes cleanly now that atomic writes +
float32 downcasting are in place.

## First time setup

```powershell
git clone <this repo's remote>   # once pushed — see "Sending results back" below
cd WhaleSweep
py -3 -m pip install --quiet numpy pandas pyarrow

# Point at the VPS's Dukascopy cache (same env var convention as
# MeanReversion's MR_DUKASCOPY_ROOT). Confirmed 2026-09-19 against Tim's
# own directory listing:
$env:WS_DUKASCOPY_ROOT = "C:\Users\Administrator\RCTBE\data\dukascopy"

.\precompute_all.ps1        # precomputes all 7 assets x 3 timeframes
py -3 selftest.py           # cheap plumbing check — run this before trusting a long search
```

**Stock codes**: `precompute.py`'s `STOCK_ASSETS` dict now has just
`AAPL` -> `AAPLUSUSD`, matching the VPS cache's real folder name
(confirmed against Tim's own directory listing 2026-09-19).
`precompute_all.ps1` will warn (not crash) on any asset it can't find.

## Freeing disk space (if you hit "No space left on device" again)

The 2026-09-19 run ran out of disk partway through the old 18-asset
sweep, which also left a few corrupted `.parquet` files behind (writes
weren't atomic at the time — fixed now, see "Robustness fixes" below).
Since only 8 assets matter going forward, delete the rest to reclaim
space (note GER40 is NOT in this list -- it's back in scope):

```powershell
Remove-Item ws_precomputed_USDJPY_*.parquet, ws_precomputed_AUDUSD_*.parquet, `
    ws_precomputed_NZDUSD_*.parquet, ws_precomputed_USDCAD_*.parquet, `
    ws_precomputed_USDCHF_*.parquet, ws_precomputed_FRA40_*.parquet, `
    ws_precomputed_UK100_*.parquet, ws_precomputed_JPN225_*.parquet, `
    ws_precomputed_WMT_*.parquet, ws_precomputed_XOM_*.parquet, `
    ws_precomputed_DIS_*.parquet `
    -ErrorAction SilentlyContinue
```

This also clears out any partially-written/corrupt files from the
disk-full incident (they'll be for the dropped assets anyway). If
`selftest.py` still errors on a `Parquet magic bytes not found in
footer` for one of the 7 kept assets, delete just that asset's `.tmp`
or `.parquet` file and re-run `precompute_all.ps1` — the pipeline now
writes atomically (see below) so a fresh run won't leave another
corrupt file even if disk fills up again.

## Robustness fixes (2026-09-19)

`precompute.py` used to write each `ws_precomputed_*.parquet` file
directly to its final path. A disk-full error mid-write left a
truncated, permanently corrupt file there, which then crashed
`selftest.py` on read. Fixed by:

- Writing to a `.tmp` file first, then `os.replace()`-ing it into place
  atomically — a failed write now leaves no file at the final path
  instead of a corrupt one.
- Downcasting OHLC/indicator float64 columns to float32 before saving,
  which also shrinks each file by roughly 27% (tested: EURUSD 1min
  53.98MB -> 39.7MB), reducing the odds of hitting the disk limit at
  all.

## Running the search

```powershell
.\start_search_4x.ps1                        # 4-way parallel, 100k-iteration ceiling per process
.\start_search_4x.ps1 -Groups 6 -Iterations 50000
```

Splits the 8-asset universe round-robin across `$Groups` processes,
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

## When the search finishes — merge the groups first

Each of the `$Groups` parallel processes writes its OWN
`whale_sweep_output_group<N>\top_strategies.json` (its own local
top-50), so with the 8-asset universe split across 4 groups, each
group's file already mixes 2 different (asset, timeframe) pairs by
score. This is the exact same "N parallel workers each with their own
ranked pool" shape RCTBE's own Layer 2 search hit (`_merge_layer2_
results.py`) — its own finding was that a pure global score cap can let
one asset with an inflated score consume nearly every slot, leaving
other assets with zero surviving candidates even when they had a real
edge. `merge_search_results.py` ports that fix: it concatenates all
groups, keeps the global top-50 by score, ALSO keeps each (asset, tf)
pair's own top-10 regardless of how the global ranking looks (WhaleSweep's
finer key, since entry_timeframe is itself swept — RCTBE only floors by
asset), dedupes, and writes the result to
`whale_sweep_output\top_strategies.json` — exactly the default path
`gate2_holdout.py`/`plateau_check.py`/`candidate_report.py` already
expect, so no `--top-json` flag is needed after this:

```powershell
py -3 merge_search_results.py
```

It also writes `whale_sweep_output\top_strategies.json.old_global.json`
— the naive pure-global-top-50 version with no per-(asset,tf) floor —
purely so you can diff the two and see which candidates the floor
rescued. Only the non-`.old_global` file is meant to be committed/used
going forward.

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
not the average, per that file's own docstring). Run this for every
(asset, tf) pair that has real candidates in the merged file, not just
whichever one happens to be ranked #1 overall — the per-key floor above
exists precisely so a lower-global-score-but-still-real candidate on
another asset doesn't get skipped just because it wasn't in the top 5.

## Sending results back

```powershell
git add ws_precomputed_*.parquet whale_sweep_output/top_strategies.json whale_sweep_output_group*/top_strategies.json
git commit -m "WhaleSweep search results"
git push
```

The merged `whale_sweep_output\top_strategies.json` is the one that
matters going forward (and what the gate scripts read by default); each
group's own file is kept alongside it purely as the raw/unmerged
record, same "only the precomputed data + the ranked results are worth
versioning" convention as MeanReversion — everything else under
`whale_sweep_output_group*/` is gitignored as regenerable working state.

Then, back on the laptop:

```powershell
cd C:\Users\leach\WhaleSweep
git pull
```

That brings down the merged `top_strategies.json`, each group's raw
file, and (if force-added) the precomputed parquet files — enough to
re-run `gate2_holdout.py`/`plateau_check.py`/`candidate_report.py`
locally against the exact same data the VPS searched, without needing
the VPS connection at all.
