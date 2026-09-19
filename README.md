# WhaleSweep — institutional liquidity-sweep reversal strategy

Trades the idea that price is drawn to rest above/below recent daily
extremes ("whale"/institutional liquidity pools), sweeps that liquidity
with a stop-hunt-style spike, then reverses. Entry is the reversal, not
the sweep itself. Built on the same precompute → swept-parameter-search →
FTMO-robustness-check pipeline as `../MeanReversion` (see that project's
`mean_reversion.py`, `ftmo_challenge_rules.py`, `VPS_DEPLOYMENT.md` for the
established conventions this project reuses rather than reinvents).

## Rules (as given by Tim, 2026-09-18)

1. **Liquidity levels considered per trading day** (a "trading day" =
   00:00–24:00 America/New_York, so this also anchors clean session
   boundaries for round-the-clock forex/index instruments):
   - `PDH` / `PDL` — previous trading day's high / low.
   - `PDH2` / `PDL2` — the high / low of the day before that.
   - `SWING_HIGH_ABOVE_PDH` — nearest confirmed swing high (K=3 fractal,
     `swing_lookback_days` back) that sits above PDH — deeper resting
     sell-side liquidity above the immediate previous-day high.
   - `SWING_LOW_BELOW_PDL` — nearest confirmed swing low below PDL,
     symmetric.
   - **Added 2026-09-19** (`include_session_levels`, per Tim — real
     intraday liquidity pools, not just the daily PDH/PDL family):
     `ASIAN_HIGH`/`ASIAN_LOW` (the Asian session's high/low, 18:00–03:00
     NY), `LONDON_HIGH`/`LONDON_LOW` (03:00–08:00 NY), and
     `PREV_WEEK_HIGH`/`PREV_WEEK_LOW` (the prior COMPLETE week's
     high/low).
   At any point in time each of these levels is classified as **upside**
   (above current price — sell-side liquidity, sweeping it sets up a
   SHORT) or **downside** (below current price — buy-side liquidity,
   sweeping it sets up a LONG), evaluated dynamically rather than assumed
   fixed, since e.g. PDH2 is not always above PDH.
2. **Entry timeframe**: swept via `entry_timeframe` — 1min, 3min, or 5min.
   Level detection itself is always computed on daily bars, independent of
   the entry timeframe used to trade the reaction.
3. **Session window**: originally right after NY cash-equity open only
   (fixed 09:30 start, 11:00-max end) — **as of 2026-09-19, per Tim, both
   ends are swept** (`session_start_minutes`: 03:00/05:00/07:00/09:30 NY;
   `session_end_minutes`: 10:30 through 16:00 NY) since the old fixed
   9:30-only start / 13:00-max end excluded the entire London session and
   NY afternoon — the single biggest cause of unrealistically low trade
   frequency (see "2026-09-19: trade-frequency overhaul" below).
4. **Sweep**: a level is "swept" the first time an entry-timeframe bar's
   high trades above an upside level (or low trades below a downside
   level) during the session window. By default each level can trigger at
   most one trade per day; **as of 2026-09-19**, `allow_level_rearm=True`
   lets a level re-arm once price closes back to its inside and be swept
   again later the same session. Overall trades/day also capped
   (`max_trades_per_day`).
5. **Confirmation** (`confirmation_mode`, one required per candidate —
   swept to find which one(s) actually have edge):
   - `immediate_break` — enter on the sweep bar itself, no extra
     confirmation (most aggressive).
   - `close_beyond` — the level must be decisively *closed* through
     (not just wicked), not merely touched.
   - `large_wick_reject` — the sweep bar's wick beyond the level is
     >= `wick_atr_mult` × ATR while its **close stays on the original
     side** (classic rejection/stop-hunt candle).
   - `reversal_cross_back` — after the sweep, price must trade back
     across the level within `reversal_lookback_bars` bars — either just
     crossing back or (`reversal_requires_close=True`) actually closing
     back on the original side.
   - `break_of_structure` — after the sweep, require a break of market
     structure in the trade direction within `bos_lookback_bars` bars,
     reusing MeanReversion's own `bos_mode` (`fractal`/`raw_wick`)
     convention.
6. **Extra confirmation — RSI** (`require_rsi_confirm`, optional, stacks
   on top of whichever `confirmation_mode` fired): `overbought_oversold`
   (RSI beyond `rsi_ob`/`rsi_os` at confirmation) or `cross_50` (RSI
   crosses back through 50 in the trade direction at confirmation).
7. **Direction**: mechanical, not a free parameter — sweeping an upside
   level → Short; sweeping a downside level → Long.
8. **Stop-loss**: placed beyond the swept level by `sl_atr_buffer_mult` ×
   ATR. If `sl_extend_to_next_level=True` and another liquidity level on
   the same (stop) side sits within `sl_extend_check_atr_mult` × ATR of
   that initial stop, the stop is pushed out to beyond *that* level
   instead — so a nearby second liquidity pool doesn't clip the trade.
9. **Take-profit** (`tp_mode`):
   - `fixed_rr` — `rr` × the SL distance.
   - `reversal_to_open` — the day's opening price (fair-value anchor).
   - `opposite_level` — the nearest liquidity level on the opposite side
     of price from entry.
10. **Assets** (narrowed 2026-09-19 per Tim from an earlier 18-asset
    universe, GER40/DAX added back same day): EURUSD, GBPUSD, XAUUSD
    (gold), NDX100, SPX500, US30, GER40 (DAX), AAPL — 8 total. All but
    AAPL are cached locally on this laptop; AAPL's ~9.3yr 1-min history
    lives only in the VPS's Dukascopy cache — see `VPS_DEPLOYMENT.md`.
11. **Target**: FTMO prop-firm rules (1-step trailing-10% and 2-step
    5%+10%-static challenges), reusing `ftmo_challenge_rules.py` verbatim
    from MeanReversion (same "duplicated, not imported" convention — see
    that file's own header).

## Assumptions made (not pinned by the brief — all easy to change)

- **"Trading day" boundary** = America/New_York midnight, not UTC
  midnight (MeanReversion's own precompute uses UTC-date boundaries,
  which is fine for its fixed-9:30-anchor fair-value logic, but would
  misalign PDH/PDL for a round-the-clock forex pair against an "after
  NY open" entry concept — so WhaleSweep's day boundary is deliberately
  NY-local instead).
- **Swing lookback**: `SWING_HIGH_ABOVE_PDH`/`SWING_LOW_BELOW_PDL` search
  back `swing_lookback_days` calendar trading days (default 20) for the
  nearest qualifying confirmed fractal; if none exists in that window,
  that one level is simply absent for the day (not an error).
- **Same-bar SL/TP collision**: SL assumed hit first (same conservative
  convention as MeanReversion).
- **One trade per level per day**, but a day can produce multiple trades
  across different swept levels, up to `max_trades_per_day`.

## Status (2026-09-19)

Pipeline built and validated end-to-end on a 3-year local subset of
EURUSD and NDX100 (all 3 entry timeframes): `precompute.py` ->
`whale_sweep.py` (signal generation + backtest replay + randomized
walk-forward search) -> `gate2_holdout.py` / `plateau_check.py` /
`candidate_report.py` (FTMO historical replay). `selftest.py` passes —
~16,000 trades generated across all 5 confirmation_modes x both assets
x all 3 timeframes with zero SL-side / R-multiple-sign invariant
violations.

**Bug found + fixed during validation**: the initial stop-loss logic
anchored the stop on the *static* swept level (level +/- an ATR
buffer). For `immediate_break`/`close_beyond`-style entries, a
breakout candle that overran the level by more than the buffer could
put the stop on the WRONG side of entry (caught via a smoke-test trade
with a positive R-multiple on an "SL" outcome). Fixed by anchoring the
stop on the actual price extreme reached between the sweep and the
entry bar instead of the static level — see `_compute_sl`'s docstring
in `whale_sweep.py`.

A 100-iteration smoke search (3yr EURUSD+NDX100 only -- NOT a real
result) surfaced one candidate (NDX100, 1min, `reversal_cross_back`,
`opposite_level` TP) with `min_profit_factor`=1.06 and 5/5 walk-forward
periods passing -- promising as a sign the mechanism can find something,
but on far too little data/breadth to mean anything on its own. It has
**not** been run through the FTMO historical-replay gate yet.

**Asset universe narrowed then GER40 added back (2026-09-19)**: down
from an earlier 18-asset plan to 7 -- EURUSD, GBPUSD, XAUUSD, NDX100,
SPX500, US30, AAPL -- per Tim's request, then GER40 (DAX) added back
the same day so the 4-way parallel VPS launcher's 4th group (which had
been left with NDX100 alone) has a second asset. `precompute.py`,
`precompute_all.ps1`, and `start_search_4x.ps1` all reflect this
8-asset universe.

**VPS disk-full incident + fix (2026-09-19)**: the first full VPS
precompute run (against the old 18-asset universe) ran out of disk
partway through, and because `precompute.py` wrote each `.parquet`
file directly to its final path, the failure left a few permanently
corrupted files behind (`selftest.py` then crashed on
`Parquet magic bytes not found in footer`). Fixed by writing to a
`.tmp` file and `os.replace()`-ing it into place atomically, plus
downcasting float64 columns to float32 before saving (~27% smaller
files, e.g. EURUSD 1min 53.98MB -> 39.7MB). See `VPS_DEPLOYMENT.md`
for the disk-cleanup + re-run steps.

**Not yet done**: the real 10-year, 8-asset search -- this needs the
VPS, both for the compute and for AAPL's data, which this laptop's
cache doesn't have. See `VPS_DEPLOYMENT.md`. Every serious candidate
that search produces should go through `candidate_report.py`'s three
gates (OOS holdout, parameter plateau, FTMO historical replay -- trust
the worst-window pass rate, not the average) before any real
risk is sized against it.

**2026-09-19: trade-frequency overhaul (per Tim: "liquidty sweeps are
things that happen near daily... how can we improve the number of
trades?")**

A fine-grained `risk_sweep.py --pick` run surfaced a real bug (days-to-pass
was counting trading days, not calendar days — a reported "5-6 days" for a
0.19-trades/week candidate was actually ~26 real calendar days) which,
once fixed, exposed the real underlying problem: candidates were trading
far too rarely to be a credible model of "liquidity sweeps happen near
daily." Root causes diagnosed and fixed, all per Tim's explicit sign-off:

- **Narrow session window** — was fixed at 09:30 NY start, capped at 13:00
  NY end (a 1-3.5hr window that excluded London and the NY afternoon
  entirely). `session_start_minutes` is now swept alongside
  `session_end_minutes` (now extending to 16:00 NY).
- **Levels usable only once/day** — a swept level was permanently
  disabled for the rest of the day even if the sweep never led to a
  trade. `allow_level_rearm=True` lets it re-arm once price closes back
  to the inside and be tested again later the same session.
- **Only the daily PDH/PDL family** — `include_session_levels=True` adds
  real intraday liquidity pools: Asian/London session H/L and the prior
  complete week's H/L (see rule 1 above).
- **Search barely penalized low frequency** — `log1p(n_trades)` in the
  score formula made 100 vs. 1000 trades differ by only ~1.5x, so nothing
  pushed the search away from candidates trading a handful of times a
  year. `MIN_TRADES_PER_WEEK` (default 2.0, `WS_MIN_TRADES_PER_WEEK`) now
  hard-rejects any candidate below that pace in `backtest_multiperiod`
  (used by both the search's accept gate and `plateau_check.py`'s
  neighbor checks).
- **10 years of history diluted recent behavior** — truncated to the most
  recent `WS_HISTORY_YEARS` (default 2.0) in `load_precomputed()`, the
  single shared loader every script in this pipeline uses, so the cut
  cascades everywhere automatically. Per Tim's explicit choice, accepting
  that this makes the rolling worst-window methodology's original
  24-month window degenerate against a 2-year-total history (exactly one
  window, nothing to compare "worst" against) — adapted by shrinking
  `ROLLING_WINDOW_MONTHS` to 12 (configurable via
  `WS_ROLLING_WINDOW_MONTHS`) rather than replacing the methodology, so
  the existing `>=8`/`>=30` resolved-cohort thresholds stay meaningful.

Verified end-to-end on a real local 2.5yr EURUSD 5min precompute: with
all 5 changes active together, a representative candidate went from
0.19 trades/week (the case that triggered this investigation) to
**13.92 trades/week**, days-to-pass dropped to a realistic 1-5 calendar
days, and `gate2_holdout.py`/`plateau_check.py`/`candidate_report.py`/
`screen_all_candidates.py`/`risk_sweep.py`/`tier_report.py` all ran
clean against it. **Every existing VPS precomputed `.parquet` file and
`top_strategies.json` predates this change** (old 10yr history, no
session-level columns, no frequency gate) — see "Re-running after the
2026-09-19 trade-frequency overhaul" in `VPS_DEPLOYMENT.md` before
trusting or extending any prior search result.

Results and any rule changes get logged here as dated update notes,
same convention as MeanReversion's own README.
