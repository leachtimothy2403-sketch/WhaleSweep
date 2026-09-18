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
   At any point in time each of these 6 levels is classified as **upside**
   (above current price — sell-side liquidity, sweeping it sets up a
   SHORT) or **downside** (below current price — buy-side liquidity,
   sweeping it sets up a LONG), evaluated dynamically rather than assumed
   fixed, since e.g. PDH2 is not always above PDH.
2. **Entry timeframe**: swept via `entry_timeframe` — 1min, 3min, or 5min.
   Level detection itself is always computed on daily bars, independent of
   the entry timeframe used to trade the reaction.
3. **Session window**: right after NY cash-equity open. Default
   09:30–11:00 America/New_York, both ends configurable
   (`session_start`/`session_end_minutes`).
4. **Sweep**: a level is "swept" the first time an entry-timeframe bar's
   high trades above an upside level (or low trades below a downside
   level) during the session window. Each level can trigger at most one
   trade per day (`one_trade_per_level`, default True); overall trades/day
   also capped (`max_trades_per_day`).
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
10. **Assets**: forex majors + the 7 index CFDs already cached locally via
    Dukascopy (~10yr history on most — see `precompute.py`'s own
    docstring for the couple of shorter-history exceptions), plus stock
    CFDs (AAPL, WMT, XOM) whose ~10yr 1-min history lives in the VPS's
    Dukascopy cache, not the local laptop's — see `VPS_DEPLOYMENT.md`.
    Tim asked for WBD (Discovery) too, but the VPS's actual cache
    (confirmed 2026-09-19) has no WBD folder — it has DISUSUSD (Disney)
    instead, so DIS is used in WBD's place; flag if a WBD folder shows up
    later and this should be swapped back.
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

A 100-iteration smoke search (3yr EURUSD+NDX100 only — NOT a real
result) surfaced one candidate (NDX100, 1min, `reversal_cross_back`,
`opposite_level` TP) with `min_profit_factor`=1.06 and 5/5 walk-forward
periods passing — promising as a sign the mechanism can find something,
but on far too little data/breadth to mean anything on its own. It has
**not** been run through the FTMO historical-replay gate yet.

**Not yet done**: the real 10-year, 18-asset (7 forex + 7 indices + 4
stocks) search — this needs the VPS, both for the compute and for the
stock data this laptop's cache doesn't have. See `VPS_DEPLOYMENT.md`.
Every serious candidate that search produces should go through
`candidate_report.py`'s three gates (OOS holdout, parameter plateau,
FTMO historical replay — trust the worst-24-month-window pass rate, not
the average) before any real risk is sized against it.

Results and any rule changes get logged here as dated update notes,
same convention as MeanReversion's own README.
