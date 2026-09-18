# Running WhaleSweep on the VPS

The full 10-year, multi-asset parameter search is meant to run on the
VPS, not this laptop — same reasoning as `../MeanReversion/VPS_DEPLOYMENT.md`:
it's faster hardware, and this laptop's local Dukascopy cache doesn't
have AAPL, which the VPS's cache does. This laptop was used only to
build and validate the pipeline (see README.md's "Status" section).

## Asset universe (narrowed 2026-09-19 per Tim)

Down from an earlier 18-asset universe to exactly 7: **EURUSD, GBPUSD,
XAUUSD (gold), NDX100, SPX500, US30, AAPL**. `precompute.py`,
`precompute_all.ps1`, and `start_search_4x.ps1` all reflect this list.
If the VPS still has precomputed files or Dukascopy cache entries for
the dropped assets (USDJPY, AUDUSD, NZDUSD, USDCAD, USDCHF, GER40,
FRA40, UK100, JPN225, WMT, XOM, DIS), they're simply ignored now —
harmless to leave in place, but see "Freeing disk space" below if
space is tight.

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
Since only 7 assets matter going forward, delete the rest to reclaim
space:

```powershell
Remove-Item ws_precomputed_USDJPY_*.parquet, ws_precomputed_AUDUSD_*.parquet, `
    ws_precomputed_NZDUSD_*.parquet, ws_precomputed_USDCAD_*.parquet, `
    ws_precomputed_USDCHF_*.parquet, ws_precomputed_GER40_*.parquet, `
    ws_precomputed_FRA40_*.parquet, ws_precomputed_UK100_*.parquet, `
    ws_precomputed_JPN225_*.parquet, ws_precomputed_WMT_*.parquet, `
    ws_precomputed_XOM_*.parquet, ws_precomputed_DIS_*.parquet `
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

Splits the 7-asset universe round-robin across `$Groups` processes,
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
