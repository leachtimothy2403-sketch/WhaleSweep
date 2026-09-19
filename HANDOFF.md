# WhaleSweep — session handoff (2026-09-20)

This file is for whoever (human or Claude session) picks this project up
next. It covers what WhaleSweep is, how the pieces fit together, exactly
what happened in the most recent working session, what we found, and what
is still open. Read this before README.md/VPS_DEPLOYMENT.md if you're
trying to figure out "where did we leave off."

## What WhaleSweep is

An institutional-liquidity-sweep reversal trading strategy, being tuned
for an FTMO prop-firm challenge (currently targeting the **2-step,
5%+10%, static-floor** challenge type). Tim (the owner) wants a small
**portfolio** of decorrelated (asset, session) candidates, Kelly-weighted
against each other, sized so the portfolio's overall historical average
pass rate lands near **50%** while passing **as fast as possible**.

Two environments, never conflated:
- **Laptop**: `C:\Users\leach\WhaleSweep`, Tim's machine, reachable from a
  Claude session via the `mcp__remote-devices__device_bash` bridge when
  linked. Used for light/fast iteration and code changes.
- **VPS**: `C:\Users\Administrator\WhaleSweep`, a separate clone, **not
  reachable from a Claude session at all**. Tim runs everything there
  himself (PowerShell) and pastes terminal output back into chat. This is
  where all heavy search compute and the real (long-history) precomputed
  data live.

**Git discipline**: a Claude session only ever commits locally on the
laptop clone. **Never `git push`** — Tim always pushes himself, from
either machine. The two clones diverge periodically (both build on a
shared ancestor but each side commits independently); resolve with
`git pull --no-rebase` (has always produced clean merges so far, since
diverging commits touched disjoint files). Windows-vs-Linux checkouts of
`whale_sweep_output/portfolio_optimizer.csv` sometimes show as "modified"
with zero real content difference — that's a CRLF/LF artifact, fix with
`git checkout -- whale_sweep_output/portfolio_optimizer.csv`, don't
investigate it as a real diff.

## Pipeline, in order

1. `precompute_all.ps1` → `precompute.py` — builds
   `ws_precomputed_<ASSET>_<TF>.parquet` per (asset, timeframe) from raw
   Dukascopy data. **Only the VPS has the full-history versions of these
   now** (confirmed 2026-09-19/20: real range is 2016-05-27 to
   2026-08-21, ~10.24 years, for EURUSD/GBPUSD/XAUUSD/US30/GER40; SPX500
   trails off a bit earlier, 2026-07-07). **The laptop's cached parquets
   are stale/shorter** — as of this handoff it only has EURUSD (from
   2024-02-27), GER40 (2022-06-07), XAUUSD (2022-04-13), and NDX100
   locally; **GBPUSD, US30, SPX500, and AAPL parquets don't exist on the
   laptop at all.** Any analysis that needs the real full picture, or
   needs GBPUSD/US30/SPX500/AAPL, has to run on the VPS — a Claude session
   working from the laptop should say so explicitly rather than silently
   reporting a partial answer (see `portfolio_report.py`'s SKIP handling
   for the pattern to follow).
2. `.\start_search_4x.ps1` → `whale_sweep.py` — the actual randomized
   parameter search, 4-way parallel by default, one process per asset
   bucket, each writing to its own `whale_sweep_output_group<N>/` with
   its own `checkpoint.json`. **Checkpointing gotcha (found live
   2026-09-19, not yet fixed in code — see "Known issues" below): the
   checkpoint has no idea what environment variables produced it.** If
   you change `WS_HISTORY_YEARS` (or `WS_IS_ONLY`, `WS_ASSETS`, etc.) and
   relaunch against the SAME output directories, and those directories
   already have a checkpoint sitting at the iteration ceiling, the search
   will "resume", see `start_iter == n_iterations`, do **zero** new
   work, and print a "Search complete" using the OLD stale numbers —
   with no warning that nothing actually happened. Always rename/move
   the old `whale_sweep_output_group*` directories aside (or point
   `WS_OUTPUT_DIR` somewhere new) before relaunching with different
   env vars.
3. `merge_search_results.py` — merges per-group results into one
   `whale_sweep_output/top_strategies.json` (global top-50 +
   per-(asset,tf) floor-of-10). The real current merged pool (as of the
   2026-09-19 run, still 2yr/default-window) has 110 rows across 8 assets
   x up to 3 timeframes; see README.md for the exact per-(asset,tf) row
   counts if needed.
4. `screen_all_candidates.py` — runs gate2_holdout + plateau_check +
   candidate_report's historical_replay_check against **every** merged
   candidate (not just the top few by score), writes
   `whale_sweep_output/candidate_screen.csv`. This is what
   `portfolio_optimizer.py`/`portfolio_report.py` join back onto
   `top_strategies.json` via the exact `local_rank` (file-order index
   within an (asset, tf) group) convention — see
   `portfolio_optimizer.load_candidate_pool()` if you need to replicate
   this join elsewhere.
5. `tier_report.py` / `risk_sweep.py` — cheap re-analysis views over an
   already-screened pool (tiering by worst-window pass rate; sweeping
   risk levels per single candidate). Not touched this session, mentioned
   for completeness.
6. **`portfolio_optimizer.py`** (added 2026-09-19) — the multi-asset
   piece. Filters to gate2+plateau PASS, dedups to one candidate per
   (asset, `session_start_minutes`) slot, computes each survivor's real
   trade records + a Kelly fraction (`argmax_f E[log(1+f*R)]`, golden-
   section search, capped), builds a correlation matrix, then for every
   combo size 2..6 finds the risk-multiplier `k` (applied as
   `risk_pct = k * kelly_f` per candidate) that lands the combo's overall
   historical pass rate near the 50% target — reporting the fastest
   (lowest median days-to-pass) combo per size, with its
   `avg_pairwise_corr` printed alongside so a fast answer is never
   silently also a correlated one.
7. **`portfolio_report.py`** (added 2026-09-20) — deep-dive on ONE
   already-chosen combo: trades/day, the full days-to-pass percentile
   spread (not just the median), and a pass-rate breakdown over the last
   5 years, last 10 years, and non-overlapping N-year buckets across
   whatever history is actually loaded. Deliberately does **not** refit
   Kelly/`k` against a longer window — it re-simulates the SAME
   already-chosen fixed risk_pct per asset over more history, as a
   robustness check, not a re-optimization. The combo it analyzes is
   hardcoded in `DEFAULT_COMBO` at the top of the file — **update this if
   the chosen portfolio changes** (see "Current best portfolio" below for
   what it's set to right now).

## What happened this session, step by step

1. Verified and committed a real bug fix to `portfolio_optimizer.py`
   (commit `d333204`): the risk-multiplier grid (`k_grid`) was a fixed
   absolute range (`geomspace(0.05, 6.0, 22)`), computed once globally
   rather than per combo. Aggregate portfolio risk scales with both combo
   size and each candidate's own Kelly fraction, so the true ~50%-pass-
   rate crossing shifts to lower `k` as combos get bigger/more
   aggressive — for large enough combos it fell entirely below the old
   grid's floor, so those combos silently reported zero valid risk
   levels (this is exactly what Tim's first real VPS run showed: only
   size-2 combos found anything, sizes 3-6 found nothing out of 119
   combos evaluated). Fixed by scaling the grid per-combo by that combo's
   mean Kelly fraction, targeting a fixed range of *average per-trade
   risk_pct* (0.02%-10% by default, now CLI-configurable via
   `--min-risk-pct`/`--max-risk-pct`/`--k-grid-points`). Verified against
   real local trade data before committing: a 4-candidate combo went from
   0 crossings (old grid) to 1 (new grid, k=0.054, pass=48.6%); several
   2-candidate pairs also revealed a SECOND, much slower low-risk
   crossing (median ~320-360 days, mostly "still going") that the old
   grid also missed — confirms the pass-rate-vs-risk curve really is
   non-monotonic in this data, not just in theory. The existing "fastest
   per size" summary logic already picks the faster of multiple
   crossings automatically, so no further code change was needed there.
2. Tim re-ran `portfolio_optimizer.py` on the VPS with the fix. Result:
   119 combinations evaluated, **119 risk levels found** (every combo
   now finds a crossing, vs. only 2 before). 7 candidates survived
   gate2+plateau+dedup: EURUSD/5min, GBPUSD/5min, GER40/3min, NDX100/5min,
   SPX500/5min, US30/3min, XAUUSD/5min — **all seven at
   `session_start_minutes=180`** (03:00 NY). See "Known issues /
   limitations" — this means Tim's original ask for NY/London/Asian
   session diversity is currently unsatisfiable from this candidate pool.
3. Analyzed the full 119-row CSV (not just the printed "fastest per
   size" summary): `median_days_to_pass` is essentially flat at 10
   across nearly every combo and size (std 0.35) — the FTMO min-days
   floor (4+4=8 trading days) dominates, so "speed" isn't really a lever
   once you're near the 50% target; the real differentiators are
   `avg_pairwise_corr` and each candidate's underlying sample size.
   Flagged that **NDX100's surviving candidate rests on only `ww_n=8`**
   (the bare minimum the gate allows) vs. `ww_n=53` for the other six —
   thin evidence relative to the rest of the pool. Combos excluding
   NDX100 hit the same ~10-day median and the same ~50% target with
   equal or better correlation, so dropping it costs nothing. Standout
   3-asset alternative identified: **GBPUSD+GER40+XAUUSD** (avg_corr
   0.063, pass=50.0% exactly, n_cohorts=104, k=0.023, per-asset risk_pct
   GER40 1.22% / GBPUSD 1.05% / XAUUSD 1.67%).
4. Tim asked for trades/day, the full days-to-pass distribution, and 5yr/
   10yr performance for the current 6-asset combo
   (EURUSD+GBPUSD+GER40+SPX500+US30+XAUUSD). Wrote `portfolio_report.py`
   for this (commit `c1d124b`), since neither the laptop (missing 3 of
   the 6 assets' parquets) nor `portfolio_optimizer.py` (only reports a
   single median) could answer it directly. Smoke-tested locally against
   the 3 assets available here before handing Tim the VPS command.
5. **Bug in my own instructions, found and fixed**: told Tim to set
   `set WS_HISTORY_YEARS=0` for "Windows" — wrong syntax for PowerShell
   (his actual shell — `set` there is aliased to `Set-Variable`, a
   PowerShell-only variable, NOT a real process environment variable, so
   Python's `os.environ.get(...)` never saw it). His first run silently
   fell back to the default 2-year truncation, producing a ~2.12-year
   span that closely matched the already-known 2yr numbers. Confirmed by
   reproducing the exact truncated date locally. Correct syntax:
   `$env:WS_HISTORY_YEARS="0"`. Patched `portfolio_report.py` (commit
   `7bff3bf`) to print `ws.HISTORY_YEARS` up front and, per candidate,
   both the truncated range actually used AND the untruncated parquet's
   raw range, so this class of mistake is visible in the output itself
   from now on rather than needing to be diagnosed after the fact.
6. Tim re-ran with the corrected syntax. **Real result: the data
   genuinely spans 2016-05-27 to 2026-08-21 (10.24 years)** for this
   combo. Trades/day ~10.1 per trading day across the merged 6-asset
   book. Days-to-pass extremely tight everywhere (median 10, p90 10-11,
   max 11-12) — once all 6 assets are contributing trades, whenever the
   portfolio passes it passes almost immediately at the structural floor;
   this part of Tim's original ask ("as fast as possible") is
   effectively solved and very stable.

   **But pass rate over time is NOT stable**: full-history 52.4%,
   last-10y 52.1% (nearly the same cohort set), **last-5y only 44.1%** —
   notably below target.
7. Added a non-overlapping N-year bucket breakdown to
   `portfolio_report.py` (`--bucket-years`, default 2.0; commit
   `042054f`) to localize where the discrepancy came from, rather than
   just inferring it from the 5yr/10yr numbers. Tim re-ran; **this is the
   most important finding of the session:**

   ```
   2016-05-27 -> 2018-05-26: pass=63.5%
   2018-05-27 -> 2020-05-25: pass=56.2%
   2020-05-26 -> 2022-05-25: pass=50.0%
   2022-05-26 -> 2024-05-24: pass=40.4%   <- trough
   2024-05-25 -> 2026-05-24: pass=51.9%
   2026-05-25 -> 2026-08-21: pass=53.8%   (only 13 cohorts, noisy)
   ```

   A **steady four-bucket decline** from 63.5% down to 40.4% across
   2016-2024, then a recovery to ~52% in exactly 2024-2026. That recovery
   window is the SAME window `portfolio_optimizer.py` used to fit the
   risk multiplier `k` in the first place — so "it hits ~52% in
   2024-2026" is not independent confirmation, it's close to circular
   (k was solved for specifically to land near 50% on that window). The
   honest read: this portfolio's edge may have been eroding for years,
   and every stage of the pipeline (search, gate2, plateau, portfolio
   sizing) shares the same `HISTORY_YEARS=2.0` default, so the entire
   candidate-selection process has implicitly been fit to a locally
   favorable recent window without ever being tested against the harder
   2022-2024 stretch.
8. Given that finding, when Tim asked what to run overnight (his options:
   (a) search new assets, (b) search for genuine session diversity),
   recommended a third option instead: re-run the search itself against
   more history (a compromise of 6 years, not full 10, to bound the
   runtime increase — search cost scales roughly with bar count) so
   candidate discovery itself gets exposed to the 2022-2024 trough,
   rather than layering more assets/sessions on top of a selection
   process that might be fundamentally window-biased. Rationale: fixing
   measurement validity takes priority over expanding coverage under a
   methodology that might be flawed.
9. Tim ran `$env:WS_HISTORY_YEARS="6"` then
   `.\start_search_4x.ps1 -Iterations 100000`. **Hit exactly the
   checkpoint bug described in step 2 of the pipeline section above**:
   all 4 groups' output directories already had a completed
   100,000-iteration checkpoint from the earlier default-window search,
   so the relaunch "resumed", did zero new iterations, and reprinted the
   stale 2yr-search numbers (`n_tested=100000 n_valid=47947
   top_score=60.5216`, identical to the original run) with no warning.
   **This was caught before real time was lost**, and Tim was given the
   fix (rename the four `whale_sweep_output_group0..3` directories to
   `..._2yr_baseline` to preserve them, then relaunch fresh) as the very
   last message of the session.

## Immediate next step -- unconfirmed at handoff time

**We do not know whether Tim actually ran the rename-and-relaunch fix
before going to bed.** The exact commands given were:

```powershell
Get-Process -Name py -ErrorAction SilentlyContinue | Stop-Process
Rename-Item whale_sweep_output_group0 whale_sweep_output_group0_2yr_baseline
Rename-Item whale_sweep_output_group1 whale_sweep_output_group1_2yr_baseline
Rename-Item whale_sweep_output_group2 whale_sweep_output_group2_2yr_baseline
Rename-Item whale_sweep_output_group3 whale_sweep_output_group3_2yr_baseline
.\start_search_4x.ps1 -Iterations 100000
```

(with `$env:WS_HISTORY_YEARS="6"` needing to still be set in that same
PowerShell session — it does not persist across a new window).

**First thing a new session should do**: ask Tim whether this ran, and if
so, check `whale_sweep_output_group0\run0_console.log` (etc.) actually
shows iterations climbing from 0 (e.g. `iter 200/100000 ...`) rather than
an instant "Search complete" — that's the tell for whether the checkpoint
bug recurred. If it's still running, that's fine, just confirm it's
making real progress and let it continue.

## Current best portfolio (as of this handoff, pending the 6yr re-search)

Six-asset combo, from the (2yr-window-calibrated) 2026-09-19
`portfolio_optimizer.py` run, currently hardcoded as `DEFAULT_COMBO` in
`portfolio_report.py`:

| asset  | tf    | local_rank | risk_pct |
|--------|-------|-----------:|---------:|
| GER40  | 3min  | 4          | 1.106%   |
| EURUSD | 5min  | 6          | 0.962%   |
| GBPUSD | 5min  | 3          | 0.952%   |
| XAUUSD | 5min  | 5          | 1.518%   |
| US30   | 3min  | 7          | 0.717%   |
| SPX500 | 5min  | 5          | 1.105%   |

avg_pairwise_corr 0.111, k=0.0209, 2yr-window pass%=52.3%,
median_days_to_pass=10 — but see the bucket-decay finding above before
trusting the pass% as durable.

A 3-asset alternative that drops the thin-sample NDX100 candidate and
scored just as well on the 2yr window: **GBPUSD+GER40+XAUUSD**
(avg_corr=0.063, pass=50.0%, n_cohorts=104, k=0.023, risk_pct GER40
1.22% / GBPUSD 1.05% / XAUUSD 1.67%). Neither of these has been
re-validated against the fuller history yet — that's pending work.

## Known issues / limitations (still open)

- **Checkpoint/env-var blind spot** (found 2026-09-19, not fixed in
  code): `whale_sweep.py`'s `checkpoint.json` (see `save_checkpoint`/
  `load_checkpoint`, ~line 619-630) stores only `iteration`,
  `top_strategies`, `n_tested`, `n_valid` — no fingerprint of the env
  vars (`WS_HISTORY_YEARS`, `WS_IS_ONLY`, `WS_ASSETS`, etc.) that
  produced it. Resuming into a completed checkpoint after changing any
  of those silently no-ops instead of erroring or warning. Worth fixing
  properly (e.g. hash the relevant env vars into the checkpoint and
  refuse/warn on mismatch) rather than relying on everyone remembering
  to rename output directories by hand every time.
- **No genuine session diversity in the candidate pool**: every
  candidate found so far (across 110 raw + all screened/deduped
  candidates) sits at `session_start_minutes` in {180, 300} — nothing at
  420 (07:00 NY-ish) or 570 (09:30, NY cash open), and the search space
  cannot express a true Asian session (18:00-03:00 NY, crosses midnight)
  at all, because `generate_signals()`'s per-calendar-day windowing
  cannot span two calendar days. Tim's original ask for NY/London/Asian
  diversification is unsatisfiable until either (a) `SPACE`'s
  `session_start_minutes` options are constrained to force a supplementary
  search into 420/570 (safe, cheap, but may just find worse candidates
  the search naturally avoided), or (b) `generate_signals()` gets real
  midnight-spanning window support (a genuine code change to core
  windowing logic — deliberately NOT done unsupervised this session,
  recommended for daylight review, not an overnight job).
- **Correlation cluster caution** (found before this session, still
  relevant): ~22 candidates across GER40/EURUSD/GBPUSD/XAUUSD were found
  sharing the exact same worst-window start date (2024-08-26) and cohort
  count (53) in an earlier screening pass — likely one shared market
  regime showing up across correlated markets, not independent evidence
  of edge. `portfolio_optimizer.py`'s `avg_pairwise_corr` reporting was
  built specifically so a "fast" combo is never silently also a
  correlated (single-regime) one — always read that column.
- **Laptop parquet cache is stale/incomplete** relative to the VPS (see
  pipeline step 1 above) — don't trust a laptop-only analysis of
  GBPUSD/US30/SPX500/AAPL, or of full-history ranges for any asset,
  without cross-checking against the VPS.
- **Kelly modeling caveat** (documented in `portfolio_optimizer.py`'s own
  module docstring): Kelly here maximizes E[log(1+f*R)] assuming
  compounding, but this project's FTMO simulation uses fixed-dollar risk
  pegged to starting equity (no compounding) — Kelly fractions are used
  as a relative cross-asset weighting heuristic, not literal
  growth-optimal sizing. The actual absolute risk calibration comes from
  the empirical grid search against real FTMO floors, which IS
  empirically grounded.
- **Non-monotonic pass-rate-vs-risk**: confirmed real (not just
  theoretical) in this data — `find_target_risk_levels()` in
  `portfolio_optimizer.py` already handles this by finding every sign
  change across the k-grid rather than assuming a single root.

## Pending tasks for the next session

1. Confirm/unblock the 6-year search relaunch (see "Immediate next step"
   above) — this is the single biggest open thread.
2. Once a genuine 6-year (or longer) search completes: re-run
   `merge_search_results.py` -> `screen_all_candidates.py` ->
   `portfolio_optimizer.py` -> `portfolio_report.py` against those new
   results, and compare the bucket breakdown to today's — the real
   question is whether search-time awareness of the 2022-2024 trough
   produces candidates that hold up more evenly across all six 2-year
   buckets, not just the most recent one.
3. Decide whether to drop NDX100 from the portfolio (thin `ww_n=8`
   sample) in favor of the GBPUSD+GER40+XAUUSD 3-asset alternative, or
   another combo — ideally after re-deriving Kelly fractions/`k` from
   whatever the new search produces, not the current 2yr-window fit.
4. Investigate WHERE the 2016-2024 decay actually came from: is it broad
   across all 6 assets, or concentrated in one or two? Consider extending
   `portfolio_report.py` (or a new script) to bucket individual
   candidates, not just the merged portfolio, to localize it.
5. Decide on a path for genuine session diversity — likely start with the
   safe/cheap option (constrain `SPACE.session_start_minutes` for a
   supplementary search) before attempting the bigger
   `generate_signals()` midnight-spanning change, and do the latter with
   real review, not as an unsupervised overnight job.
6. Consider actually fixing the checkpoint/env-var fingerprinting issue
   in `whale_sweep.py` so this class of silent-no-op mistake can't
   recur — it's cheap to fix (hash a handful of env vars into
   `checkpoint.json`, compare on resume, warn or refuse on mismatch) and
   has now bitten us once already.

## File/commit reference

Commits made this session (laptop clone, `C:\Users\leach\WhaleSweep`,
not yet pushed by me — check with Tim/`git log origin/main..HEAD` whether
they've since been pushed):

- `d333204` — `portfolio_optimizer.py`: fix k_grid range bug causing
  missed crossings
- `c1d124b` — Add `portfolio_report.py`: deep-dive on a chosen combo
- `7bff3bf` — `portfolio_report.py`: print effective HISTORY_YEARS + raw
  parquet range (the PowerShell env-var fix)
- `042054f` — `portfolio_report.py`: add non-overlapping time-bucket
  breakdown

Plus one VPS-side commit pulled in: `7e1723e` (Tim's corrected
`portfolio_optimizer.csv` re-run after the k_grid fix).

## Gotchas worth remembering

- **PowerShell env vars**: `$env:VAR="value"`, never `set VAR=value`
  (that's cmd.exe syntax and silently does nothing useful in
  PowerShell — it sets a PowerShell-only variable, not a real process
  environment variable). This bit us once already (step 5 above).
- **Checkpoint + env var changes**: always use a fresh/renamed output
  directory when changing any search-affecting env var
  (`WS_HISTORY_YEARS`, `WS_IS_ONLY`, `WS_ASSETS`, iteration ceiling
  doesn't matter as much but the data-affecting ones do) — see "Known
  issues" above.
- **Never `git push` from a Claude session** — commit locally, tell Tim,
  let him push.
- **The laptop is missing GBPUSD/US30/SPX500/AAPL parquets entirely**,
  and its EURUSD/GER40/XAUUSD/NDX100 copies are shorter-history than the
  VPS's. Any laptop-only analysis of these should say so explicitly.
