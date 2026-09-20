#!/usr/bin/env python3
"""
WhaleSweep — signal engine + randomized parameter search.

Trades institutional-liquidity sweeps: price trades through a resting
daily-level liquidity pool (PDH/PDL, PDH2/PDL2, or a confirmed swing
beyond either), then a confirmation pattern fires and a trade is taken
back in the OPPOSITE direction (fade the sweep), betting the level was
a stop-hunt rather than a genuine breakout. See README.md for the full
rule set and assumptions.

Mirrors ../MeanReversion/mean_reversion.py's own conventions
(SPACE/sample_params/generate_signals/backtest_signals/
backtest_multiperiod/checkpointed random search -> CSV + top_strategies.
json) deliberately, so this project reads the same way to anyone
already familiar with that one.

Requires precompute.py to have already produced
`ws_precomputed_<ASSET>_<TF>.parquet` files in this directory.

VALIDATION STANDARD (added 2026-09-18, matching MeanReversion's own bar):
  5-period walk-forward split (N_PERIODS=5). WS_IS_ONLY=1 (default ON)
  truncates every (asset, timeframe) to periods 1-3 (the first 60% of
  history) before the SEARCH's own accept/scoring gate ever sees it --
  same fix as RCTBE_L2_IS_ONLY / MR_IS_ONLY. Without it, a candidate can
  scrape into top_strategies.json partly because it looked good on
  periods 4-5 -- the SAME bars gate2_holdout.py later calls a "blind OOS
  holdout" -- which isn't a genuinely blind test at that point, just a
  re-score of bars the search already used to select candidates (this
  was WhaleSweep's actual behavior before this fix -- see git history
  and gate2_holdout.py's now-updated docstring). With WS_IS_ONLY=1, the
  search only ever sees periods 1-3; gate2_holdout.py/plateau_check.py/
  candidate_report.py all load the FULL untruncated file directly via
  load_precomputed() (untouched by this flag -- see _search_view()),
  so periods 4-5 are genuinely blind to everything the search did.
  Set WS_IS_ONLY=0 only to deliberately reproduce the old (leakier)
  behavior for comparison.

Usage:
    py -3 whale_sweep.py                 # random search, WS_ITERATIONS (default 20000)
    WS_ITERATIONS=500 py -3 whale_sweep.py
Env vars: WS_OUTPUT_DIR, WS_ITERATIONS, WS_ASSETS, WS_IS_ONLY (default "1"),
WS_CHECKPOINT_EVERY, WS_DUKASCOPY_ROOT.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from whale_sweep_cost_table import COST_TABLE

# ══════════════════════════════════════════════════════════════════════════
#  Parameter space
# ══════════════════════════════════════════════════════════════════════════

CONFIRMATION_MODES = ["immediate_break", "close_beyond", "large_wick_reject",
                       "reversal_cross_back", "break_of_structure"]

SPACE = {
    "entry_timeframe":         ["1min", "3min", "5min"],
    "atr_window":               [14, 20, 30],
    "include_secondary_levels": [False, True],          # PDH2/PDL2 + swing-beyond levels, vs. PDH/PDL only
    # 2026-09-19, per Tim: liquidity sweeps happen against intraday levels
    # too, not just the daily PDH/PDL family -- Asian session H/L, London
    # session H/L, and the prior COMPLETE week's H/L are all real,
    # commonly-referenced liquidity pools. True adds them as additional
    # candidate levels each day (independent of include_secondary_levels).
    "include_session_levels":   [False, True],
    # 2026-09-19, per Tim: a level was previously usable only ONCE per day
    # (permanently marked used the instant it was first swept, whether or
    # not that sweep even led to a trade). True lets it re-arm once price
    # closes back to the inside and be swept+traded again later the same
    # session -- a level can legitimately get tested more than once a day.
    "allow_level_rearm":        [False, True],

    "confirmation_mode":        CONFIRMATION_MODES,
    "close_beyond_lookback_bars": [1, 2, 3, 5],          # confirmation_mode="close_beyond" only
    "wick_atr_mult":             [0.5, 0.75, 1.0, 1.5, 2.0],   # "large_wick_reject" only
    "reversal_lookback_bars":    [1, 2, 3, 5, 8, 13, 20],       # "reversal_cross_back" only
    "reversal_requires_close":   [False, True],                 # "reversal_cross_back" only
    "bos_lookback_bars":         [3, 5, 8, 13, 20, 30],         # "break_of_structure" only
    "bos_mode":                  ["fractal", "raw_wick"],       # "break_of_structure" only
    "bos_confirm_atr_mult":      [0.0, 0.1, 0.2, 0.3, 0.5],     # "break_of_structure" only

    "require_rsi_confirm":      [False, True],
    "rsi_period":                [7, 14, 21],
    "rsi_mode":                  ["overbought_oversold", "cross_50"],
    "rsi_ob":                    [65, 70, 75, 80],
    "rsi_os":                    [35, 30, 25, 20],

    "sl_atr_buffer_mult":        [0.1, 0.2, 0.3, 0.5, 0.75, 1.0],
    "sl_extend_to_next_level":   [False, True],
    "sl_extend_check_atr_mult":  [0.5, 1.0, 1.5, 2.0],

    "tp_mode":                   ["fixed_rr", "reversal_to_open", "opposite_level"],
    "rr":                        [1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0],

    # 2026-09-19, per Tim: sweeps happen near-daily in reality; a fixed
    # 9:30-only start and a window that never runs past 13:00 NY excludes
    # the entire London session and the whole NY afternoon, which was the
    # single biggest structural cause of low trade frequency (see git
    # history / the 2026-09-19 trade-frequency investigation). Now swept
    # like everything else instead of fixed.
    "session_start_minutes":     [180, 300, 420, 570],       # 03:00 (London open-ish) / 05:00 / 07:00 / 09:30 (old fixed value) NY
    "session_end_minutes":       [630, 660, 690, 750, 780, 840, 900, 960],  # 10:30 / 11:00 / 11:30 / 12:30 / 13:00 / 14:00 / 15:00 / 16:00 (NY close) NY
    "max_trades_per_day":        [1, 2, 3, 5, 999],
    "skip_weekday":              [-1, 0, 1, 2, 3, 4],        # -1 = no skip; 0=Mon..4=Fri
}

N_PERIODS = 5
MIN_TRADES_PER_PERIOD = 20
TOP_N = 50

# 2026-09-19, per Tim: liquidity sweeps happen near-daily in reality, so a
# candidate trading only a handful of times a YEAR isn't a rare gem -- it's
# very likely fitted to a few lucky historical coincidences. The old score
# formula's log1p(n_trades) term barely penalized this (100 trades vs 1000
# only differs by ~1.5x in that term), so nothing pushed the search away
# from low-frequency candidates. Hard-reject below this instead of relying
# on score to sort it out. <=0 disables the gate (old behavior).
MIN_TRADES_PER_WEEK = float(os.environ.get("WS_MIN_TRADES_PER_WEEK", "2.0"))

RNG = np.random.default_rng()


def sample_params(rng: np.random.Generator = RNG) -> dict:
    p = {k: rng.choice(v) if not isinstance(v[0], bool) else bool(rng.choice(v)) for k, v in SPACE.items()}
    # normalize numpy scalar types to plain python for clean JSON/CSV output
    p = {k: (v.item() if hasattr(v, "item") else v) for k, v in p.items()}
    return p


# ══════════════════════════════════════════════════════════════════════════
#  Data loading
# ══════════════════════════════════════════════════════════════════════════

_DF_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def discover_asset_timeframes() -> List[tuple[str, str]]:
    """(asset, timeframe) pairs with a precomputed file present in cwd.
    Restricted to WS_ASSETS (comma-separated) when set — lets a parallel
    VPS launcher split the asset universe across processes without them
    stepping on each other's search (see VPS_DEPLOYMENT.md)."""
    restrict = os.environ.get("WS_ASSETS")
    allowed = {a.strip() for a in restrict.split(",")} if restrict else None
    out = []
    # Look for files in current directory, and also check parent if run from a sub-directory.
    search_pattern = "ws_precomputed_*.parquet"
    files = glob.glob(search_pattern) + glob.glob("../" + search_pattern)
    print(f"[DEBUG] glob found {len(files)} files: {files}", flush=True)
    for f in sorted(set(files)):
        f_name = os.path.basename(f)
        m = re.match(r"ws_precomputed_(.+)_(1min|3min|5min)\.parquet$", f_name)
        if m and (allowed is None or m.group(1) in allowed):
            out.append((m.group(1), m.group(2)))
    print(f"[DEBUG] discovered assets: {out}", flush=True)
    return out


# 2026-09-19, per Tim: recent market behavior is what matters for a live
# challenge attempt, not the full 10-year backtest -- also has a useful
# side effect on trade frequency: gate2_holdout.py's OOS block and
# backtest_multiperiod's periods all get proportionally SHORTER too (a
# period is 1/5 of whatever load_precomputed() returns), so the same
# MIN_TRADES_PER_PERIOD=20 now demands a much higher trade density just
# to qualify. <=0 disables truncation (the old full-file behavior, for
# comparison/debugging only -- see git history before this change).
HISTORY_YEARS = float(os.environ.get("WS_HISTORY_YEARS", "2.0"))


def load_precomputed(asset: str, tf: str) -> pd.DataFrame:
    """Returns the precomputed file for (asset, tf), cached, truncated to
    the most recent HISTORY_YEARS of calendar time (default 2.0 -- see
    HISTORY_YEARS above). This is the SINGLE shared loader every other
    script in this project calls -- whale_sweep.py's own search (via
    _search_view(), which further truncates to periods 1-3 of WHATEVER
    this returns), gate2_holdout.py, plateau_check.py, candidate_report.py,
    screen_all_candidates.py, risk_sweep.py, tier_report.py -- so
    truncating here cascades everywhere at once rather than needing every
    consumer updated individually. Indicators (ATR/RSI/PDH/PDL/etc.) stay
    correctly warmed up regardless of where this cuts, since precompute.py
    always computes them over the FULL raw history before this ever runs;
    slicing here only ever drops calendar time, never indicator lookback.
    Deliberately still untouched by WS_IS_ONLY -- gate2_holdout.py/
    plateau_check.py/candidate_report.py need whatever this returns in
    full to do a genuinely blind OOS check within it; see _search_view()
    for the additional IS-only slice the search loop itself uses on top
    of this."""
    key = (asset, tf)
    if key not in _DF_CACHE:
        path = f"ws_precomputed_{asset}_{tf}.parquet"
        df = pd.read_parquet(path)
        if HISTORY_YEARS > 0 and len(df) > 0:
            cutoff = df.index[-1] - pd.Timedelta(days=HISTORY_YEARS * 365.25)
            df = df[df.index >= cutoff].copy()
        _DF_CACHE[key] = df
    return _DF_CACHE[key]


# Blind-search selection fix -- see module docstring's VALIDATION STANDARD
# section. Mirrors MeanReversion's MR_IS_ONLY (default "1" there too).
IS_ONLY_SEARCH = os.environ.get("WS_IS_ONLY", "1").lower() in ("1", "true", "yes")

_SEARCH_VIEW_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def _search_view(asset: str, tf: str) -> pd.DataFrame:
    """What the SEARCH loop backtests against: the full precomputed file,
    truncated to periods 1-3 (first 60% of history) when WS_IS_ONLY is on.
    Never used by gate2_holdout.py/plateau_check.py/candidate_report.py --
    they call load_precomputed() directly to get the untruncated file."""
    key = (asset, tf)
    if key in _SEARCH_VIEW_CACHE:
        return _SEARCH_VIEW_CACHE[key]
    df = load_precomputed(asset, tf)
    if IS_ONLY_SEARCH:
        n = len(df)
        bounds = np.linspace(0, n, N_PERIODS + 1).astype(int)
        df = df.iloc[bounds[0]:bounds[3]]
    _SEARCH_VIEW_CACHE[key] = df
    return df


# ══════════════════════════════════════════════════════════════════════════
#  Signal generation
# ══════════════════════════════════════════════════════════════════════════

def _resolve_confirmation(i: int, side: str, level: float, p: dict,
                           o, h, l, c, atr, tf_sh, tf_sl, limit: int) -> Optional[int]:
    """Returns the entry bar index if the confirmation mode fires within
    its lookback, else None. `limit` is exclusive upper bound (session end)."""
    mode = p["confirmation_mode"]

    if mode == "immediate_break":
        return i

    if mode == "close_beyond":
        end = min(i + p["close_beyond_lookback_bars"], limit - 1)
        for j in range(i, end + 1):
            beyond = c[j] > level if side == "upside" else c[j] < level
            if beyond:
                return j
        return None

    if mode == "large_wick_reject":
        wick = (h[i] - level) if side == "upside" else (level - l[i])
        if wick >= p["wick_atr_mult"] * atr[i]:
            stayed = c[i] < level if side == "upside" else c[i] > level
            if stayed:
                return i
        return None

    if mode == "reversal_cross_back":
        end = min(i + p["reversal_lookback_bars"], limit - 1)
        for j in range(i, end + 1):
            if p["reversal_requires_close"]:
                back = c[j] < level if side == "upside" else c[j] > level
            else:
                back = l[j] < level if side == "upside" else h[j] > level
            if back:
                return j
        return None

    if mode == "break_of_structure":
        end = min(i + p["bos_lookback_bars"], limit - 1)
        if p["bos_mode"] == "raw_wick":
            prev = max(i - 1, 0)
            ref = l[prev] if side == "upside" else h[prev]
        else:
            ref = tf_sl[i] if side == "upside" else tf_sh[i]
        if ref is None or np.isnan(ref):
            return None
        buf_mult = p["bos_confirm_atr_mult"]
        for j in range(i, end + 1):
            buf = buf_mult * atr[j]
            if side == "upside":
                if c[j] < ref - buf:
                    return j
            else:
                if c[j] > ref + buf:
                    return j
        return None

    raise ValueError(f"unknown confirmation_mode {mode!r}")


def _rsi_confirms(rsi, j: int, side: str, p: dict) -> bool:
    if j >= len(rsi) or np.isnan(rsi[j]):
        return False
    if p["rsi_mode"] == "overbought_oversold":
        return rsi[j] >= p["rsi_ob"] if side == "upside" else rsi[j] <= p["rsi_os"]
    # cross_50
    if j == 0 or np.isnan(rsi[j - 1]):
        return False
    if side == "upside":
        return rsi[j - 1] >= 50 and rsi[j] < 50
    return rsi[j - 1] <= 50 and rsi[j] > 50


def _compute_sl(direction: str, stop_ref: float, atr_j: float, p: dict, levels: list) -> float:
    """`stop_ref` is the actual price extreme reached between the sweep
    and the entry bar (not the static swept level) — guarantees the stop
    lands beyond wherever price actually got to, even when a decisive
    close-beyond/reversal-cross-back confirmation only fires several bars
    after a breakout candle that overran the level. Using the static
    level alone here was a real bug: a large breakout candle can close
    beyond level + the whole ATR buffer, producing a stop on the WRONG
    side of entry (caught via a smoke-test trade with a positive
    R-multiple on an 'SL' outcome — see git history / dev notes)."""
    buf = p["sl_atr_buffer_mult"] * atr_j
    if direction == "Sell":
        sl = stop_ref + buf
        if p["sl_extend_to_next_level"]:
            further = [lv[1] for lv in levels if lv[2] == "upside" and lv[1] > stop_ref]
            if further:
                nearest = min(further)
                check_dist = p["sl_extend_check_atr_mult"] * atr_j
                if nearest <= sl + check_dist:
                    sl = nearest + buf
    else:
        sl = stop_ref - buf
        if p["sl_extend_to_next_level"]:
            further = [lv[1] for lv in levels if lv[2] == "downside" and lv[1] < stop_ref]
            if further:
                nearest = max(further)
                check_dist = p["sl_extend_check_atr_mult"] * atr_j
                if nearest >= sl - check_dist:
                    sl = nearest - buf
    return sl


def _compute_tp(direction: str, entry: float, risk: float, p: dict,
                 session_open: float, levels: list) -> float:
    mode = p["tp_mode"]
    if mode == "reversal_to_open":
        return session_open
    if mode == "opposite_level":
        if direction == "Sell":
            cand = [lv[1] for lv in levels if lv[2] == "downside" and lv[1] < entry]
            if cand:
                return max(cand)
        else:
            cand = [lv[1] for lv in levels if lv[2] == "upside" and lv[1] > entry]
            if cand:
                return min(cand)
        # fall through to fixed_rr if no opposing level exists that day
    return entry - p["rr"] * risk if direction == "Sell" else entry + p["rr"] * risk


def generate_signals(df: pd.DataFrame, p: dict) -> List[dict]:
    n = len(df)
    o = df["open"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = df[f"atr_{p['atr_window']}"].to_numpy()
    rsi = df[f"rsi_{p['rsi_period']}"].to_numpy() if p["require_rsi_confirm"] else None
    ny_min = df["ny_minutes"].to_numpy()
    day_id = df["day_id"].to_numpy()
    weekday = pd.to_datetime(df["day_id"]).dt.weekday.to_numpy()
    pdh = df["pdh"].to_numpy(); pdl = df["pdl"].to_numpy()
    pdh2 = df["pdh2"].to_numpy(); pdl2 = df["pdl2"].to_numpy()
    swh = df["swing_high_above_pdh"].to_numpy(); swl = df["swing_low_below_pdl"].to_numpy()
    sess_open = df["session_open_930"].to_numpy()
    # Asian/London session H-L + prior-week H-L are only present in
    # parquets regenerated by the 2026-09-19 precompute.py update -- older
    # precomputed files (not yet re-run through precompute.py) won't have
    # these columns at all. Only touch them when the toggle that actually
    # uses them is on, and degrade to "column missing" (all-NaN, so the
    # level is silently skipped in the per-day loop below) rather than a
    # bare KeyError, so unrelated candidates/backtests against stale
    # parquet files keep working.
    if p["include_session_levels"]:
        na = np.full(n, np.nan)
        asian_hi = df["asian_high"].to_numpy() if "asian_high" in df.columns else na
        asian_lo = df["asian_low"].to_numpy() if "asian_low" in df.columns else na
        london_hi = df["london_high"].to_numpy() if "london_high" in df.columns else na
        london_lo = df["london_low"].to_numpy() if "london_low" in df.columns else na
        pwh = df["prev_week_high"].to_numpy() if "prev_week_high" in df.columns else na
        pwl = df["prev_week_low"].to_numpy() if "prev_week_low" in df.columns else na
    tf_sh = df["tf_swing_high_confirmed"].to_numpy(); tf_sl = df["tf_swing_low_confirmed"].to_numpy()

    in_session = (ny_min >= p["session_start_minutes"]) & (ny_min < p["session_end_minutes"])

    day_change = np.empty(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = day_id[1:] != day_id[:-1]
    day_starts = np.flatnonzero(day_change)
    day_ends = np.r_[day_starts[1:], n]

    signals: List[dict] = []

    for ds, de in zip(day_starts, day_ends):
        if p["skip_weekday"] != -1 and weekday[ds] == p["skip_weekday"]:
            continue
        block_mask = in_session[ds:de]
        if not block_mask.any():
            continue
        rel = np.flatnonzero(block_mask)
        sub_start, sub_end = ds + rel[0], ds + rel[-1] + 1   # [sub_start, sub_end) contiguous session window

        row0 = ds
        so_v = sess_open[row0]
        raw_levels = [("PDH", pdh[row0]), ("PDL", pdl[row0])]
        if p["include_secondary_levels"]:
            raw_levels += [("PDH2", pdh2[row0]), ("PDL2", pdl2[row0]),
                           ("SWING_HIGH_ABOVE_PDH", swh[row0]), ("SWING_LOW_BELOW_PDL", swl[row0])]
        if p["include_session_levels"]:
            raw_levels += [("ASIAN_HIGH", asian_hi[row0]), ("ASIAN_LOW", asian_lo[row0]),
                           ("LONDON_HIGH", london_hi[row0]), ("LONDON_LOW", london_lo[row0]),
                           ("PREV_WEEK_HIGH", pwh[row0]), ("PREV_WEEK_LOW", pwl[row0])]
        # lv[3] is a state string, not the plain "used" bool this was before
        # 2026-09-19's re-arm feature: "armed" (never swept, or swept then
        # re-closed to the inside and eligible again), "used" (swept once,
        # permanently done for the day -- the legacy/default behavior when
        # allow_level_rearm=False), or "await_rearm" (swept, waiting for
        # price to close back to the inside before it can trigger again --
        # only reachable when allow_level_rearm=True). Neither _compute_sl
        # nor _compute_tp ever reads lv[3], only lv[1]/lv[2], so this is
        # safe to repurpose.
        levels = []
        for name, val in raw_levels:
            if np.isnan(val) or np.isnan(so_v):
                continue
            side = "upside" if val > so_v else "downside"
            levels.append([name, float(val), side, "armed"])
        if not levels:
            continue

        trades_today = 0
        for i in range(sub_start, sub_end):
            if trades_today >= p["max_trades_per_day"]:
                break
            hi_, lo_, close_ = h[i], l[i], c[i]
            for lv in levels:
                name, val, side, state = lv
                if state == "await_rearm":
                    # Re-test/fakeout modeling: a level that already got
                    # swept+traded doesn't count as a fresh opportunity
                    # again until price genuinely retreats back to the
                    # inside of it (closes below a swept-upside level, or
                    # above a swept-downside one) -- otherwise the very
                    # next bar of the same continuous breakout move would
                    # "re-sweep" it, which isn't a new event at all.
                    closed_inside = (close_ < val) if side == "upside" else (close_ > val)
                    if closed_inside:
                        lv[3] = "armed"
                    continue
                if state != "armed":
                    continue
                swept = (side == "upside" and hi_ > val) or (side == "downside" and lo_ < val)
                if not swept:
                    continue
                lv[3] = "await_rearm" if p["allow_level_rearm"] else "used"
                entry_j = _resolve_confirmation(i, side, val, p, o, h, l, c, atr, tf_sh, tf_sl, sub_end)
                if entry_j is None:
                    continue
                if p["require_rsi_confirm"] and not _rsi_confirms(rsi, entry_j, side, p):
                    continue
                atr_j = atr[entry_j]
                if np.isnan(atr_j) or atr_j <= 0:
                    continue
                direction = "Sell" if side == "upside" else "Buy"
                entry_price = float(c[entry_j])
                if direction == "Sell":
                    stop_ref = float(np.max(h[i:entry_j + 1]))
                else:
                    stop_ref = float(np.min(l[i:entry_j + 1]))
                sl_price = _compute_sl(direction, stop_ref, atr_j, p, levels)
                risk = abs(entry_price - sl_price)
                if risk <= 0 or np.isnan(risk):
                    continue
                tp_price = _compute_tp(direction, entry_price, risk, p, so_v, levels)
                signals.append({
                    "day_id": str(day_id[i]), "level": name, "direction": direction,
                    "sweep_idx": int(i), "entry_idx": int(entry_j),
                    "entry": entry_price, "sl": sl_price, "tp": tp_price, "risk": risk,
                    "session_end_idx": int(sub_end - 1), "cost": COST_TABLE[p["asset"]],
                })
                trades_today += 1
                if trades_today >= p["max_trades_per_day"]:
                    break
    return signals


# ══════════════════════════════════════════════════════════════════════════
#  Backtest replay
# ══════════════════════════════════════════════════════════════════════════

def backtest_signals(signals: List[dict], df: pd.DataFrame) -> List[dict]:
    h = df["high"].to_numpy(); l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    out = []
    for sig in signals:
        entry_idx, direction = sig["entry_idx"], sig["direction"]
        entry, sl, tp, risk, cost = sig["entry"], sig["sl"], sig["tp"], sig["risk"], sig["cost"]
        end_idx = min(sig["session_end_idx"], n - 1)
        outcome, exit_idx, exit_price = "EOD", end_idx, float(c[end_idx])
        for j in range(entry_idx + 1, end_idx + 1):
            if direction == "Sell":
                hit_sl, hit_tp = h[j] >= sl, l[j] <= tp
            else:
                hit_sl, hit_tp = l[j] <= sl, h[j] >= tp
            if hit_sl:      # conservative same-bar convention: SL assumed first
                outcome, exit_idx, exit_price = "SL", j, sl
                break
            if hit_tp:
                outcome, exit_idx, exit_price = "TP", j, tp
                break
        pnl = (entry - exit_price) if direction == "Sell" else (exit_price - entry)
        pnl -= cost
        r_mult = pnl / risk if risk > 0 else 0.0
        out.append({**sig, "exit_idx": exit_idx, "exit_price": exit_price,
                    "outcome": outcome, "r_multiple": r_mult})
    return out


def summarize_trades(results: List[dict]) -> dict:
    nt = len(results)
    if nt == 0:
        return {"n_trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "total_r": 0.0,
                "expectancy_r": 0.0, "max_dd_r": 0.0, "eod_frac": 0.0}
    arr = np.array([r["r_multiple"] for r in results], dtype=float)
    wins, losses = arr[arr > 0], arr[arr <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    if gross_loss > 0:
        pf = min(gross_win / gross_loss, 999.0)
    else:
        pf = 999.0 if gross_win > 0 else 0.0
    cum = np.cumsum(arr)
    dd = np.maximum.accumulate(cum) - cum
    eod_frac = float(np.mean([r["outcome"] == "EOD" for r in results]))
    return {
        "n_trades": nt, "win_rate_pct": round(100 * len(wins) / nt, 2),
        "profit_factor": round(float(pf), 3), "total_r": round(float(arr.sum()), 2),
        "expectancy_r": round(float(arr.mean()), 4), "max_dd_r": round(float(dd.max()), 2),
        "eod_frac": round(eod_frac, 4),
    }


def get_trade_records(df: pd.DataFrame, p: dict) -> List[dict]:
    signals = generate_signals(df, p)
    return backtest_signals(signals, df)


def backtest_multiperiod(df: pd.DataFrame, p: dict, n_periods: int = N_PERIODS) -> Optional[dict]:
    results = get_trade_records(df, p)
    if len(results) < MIN_TRADES_PER_PERIOD * 2:
        return None

    if MIN_TRADES_PER_WEEK > 0:
        trade_days = sorted(pd.Timestamp(r["day_id"]).date() for r in results)
        span_days = (trade_days[-1] - trade_days[0]).days + 1
        trades_per_week = len(results) / (span_days / 7) if span_days > 0 else 0.0
        if trades_per_week < MIN_TRADES_PER_WEEK:
            return None

    bounds = np.linspace(0, len(df), n_periods + 1).astype(int)
    period_stats = []
    for k in range(n_periods):
        lo, hi = bounds[k], bounds[k + 1]
        sub = [r for r in results if lo <= r["entry_idx"] < hi]
        period_stats.append(summarize_trades(sub))

    valid = [ps for ps in period_stats if ps["n_trades"] >= MIN_TRADES_PER_PERIOD]
    if not valid:
        return None
    overall = summarize_trades(results)
    pfs = [ps["profit_factor"] for ps in valid]
    avg_pf, min_pf = float(np.mean(pfs)), float(np.min(pfs))
    passing = sum(1 for ps in valid if ps["profit_factor"] >= 1.0)
    consistency = passing / len(valid)
    calmar = overall["total_r"] / overall["max_dd_r"] if overall["max_dd_r"] > 0 else 0.0

    score = min_pf * consistency * float(np.log1p(overall["n_trades"]))
    return {
        **overall, "avg_profit_factor": round(avg_pf, 3), "min_profit_factor": round(min_pf, 3),
        "periods_evaluated": len(valid), "periods_passed": passing,
        "consistency": round(consistency, 3), "calmar": round(calmar, 3),
        "score": round(score, 4), "period_pfs": [round(x, 3) for x in pfs],
    }


# ══════════════════════════════════════════════════════════════════════════
#  Randomized search harness — mirrors MeanReversion's checkpoint/CSV/
#  top_strategies.json conventions.
# ══════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(os.environ.get("WS_OUTPUT_DIR", "whale_sweep_output"))
RESULTS_CSV = OUTPUT_DIR / "results.csv"
CHECKPOINT_PATH = OUTPUT_DIR / "checkpoint.json"
TOP_STRATEGIES_PATH = OUTPUT_DIR / "top_strategies.json"
RUN_LOG_PATH = OUTPUT_DIR / "run.log"

_CSV_HEADER_WRITTEN = {"flag": False}


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_row(row: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_row = pd.DataFrame([row])
    header = not RESULTS_CSV.exists()
    df_row.to_csv(RESULTS_CSV, mode="a", header=header, index=False)


def save_top(top: list) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOP_STRATEGIES_PATH, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, default=str)


def save_checkpoint(iteration: int, top: list, n_tested: int, n_valid: int) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "iteration": iteration, "n_tested": n_tested, "n_valid": n_valid,
            "top_strategies": top, "saved_at": datetime.now().isoformat(),
        }, f, indent=2, default=str)


def load_checkpoint() -> Optional[dict]:
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def _insert_top(top: list, row: dict, top_n: int = TOP_N) -> list:
    top.append(row)
    top.sort(key=lambda r: r["score"], reverse=True)
    return top[:top_n]


def main() -> None:
    assets = sorted({a for a, _ in discover_asset_timeframes()})
    if not assets:
        raise SystemExit("No ws_precomputed_*.parquet files found — run precompute.py first.")
    _log(f"Assets available: {assets}")
    _log(f"WS_IS_ONLY={'ON' if IS_ONLY_SEARCH else 'OFF'} — "
         f"{'every (asset, tf) truncated to periods 1-3 before the search sees it (genuinely blind Gate 2)' if IS_ONLY_SEARCH else 'search sees full history — Gate 2 later re-scores bars the accept gate already used, NOT a clean blind test'}")

    n_iterations = int(os.environ.get("WS_ITERATIONS", "20000"))
    checkpoint_every = int(os.environ.get("WS_CHECKPOINT_EVERY", "200"))

    ckpt = load_checkpoint()
    if ckpt:
        start_iter, top, n_tested, n_valid = ckpt["iteration"], ckpt["top_strategies"], ckpt["n_tested"], ckpt["n_valid"]
        _log(f"Resumed from checkpoint at iteration {start_iter} (n_tested={n_tested}, n_valid={n_valid})")
    else:
        start_iter, top, n_tested, n_valid = 0, [], 0, 0

    t0 = time.time()
    for it in range(start_iter, n_iterations):
        p = sample_params()
        asset = str(RNG.choice(assets))
        p["asset"] = asset
        tf = p["entry_timeframe"]
        path = Path(f"ws_precomputed_{asset}_{tf}.parquet")
        if not path.exists():
            continue
        try:
            df = _search_view(asset, tf)
            result = backtest_multiperiod(df, p)
        except Exception as e:   # a bad param combo should never kill an unattended multi-hour run
            _log(f"iter {it} asset={asset} tf={tf} ERROR: {e}")
            continue
        n_tested += 1
        if result is not None:
            n_valid += 1
            row = {"asset": asset, **p, **result}
            write_row(row)
            top = _insert_top(top, row)

        if (it + 1) % checkpoint_every == 0:
            save_checkpoint(it + 1, top, n_tested, n_valid)
            save_top(top)
            elapsed = time.time() - t0
            rate = (it + 1 - start_iter) / elapsed if elapsed > 0 else 0.0
            best = top[0]["score"] if top else 0.0
            _log(f"iter {it + 1}/{n_iterations} tested={n_tested} valid={n_valid} "
                 f"best_score={best} rate={rate:.2f} it/s")

    save_checkpoint(n_iterations, top, n_tested, n_valid)
    save_top(top)
    (OUTPUT_DIR / "DONE").write_text(datetime.now().isoformat())
    _log(f"Search complete. n_tested={n_tested} n_valid={n_valid} top_score={top[0]['score'] if top else None}")


if __name__ == "__main__":
    main()
