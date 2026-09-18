#!/usr/bin/env python3
"""
WhaleSweep — precompute: multi-timeframe OHLCV + RSI + ATR + daily
liquidity levels (PDH/PDL, PDH2/PDL2, swing-beyond-PDH/PDL) + session
anchors, for the institutional-liquidity-sweep reversal strategy.

Shares MeanReversion's Dukascopy data-loading conventions (divisor-break
harmonization, dead-bar drop) — copied, not imported, same "no
cross-project reliance" pattern MeanReversion's own precompute.py
documents for its own borrowing from RCTBE. See ../MeanReversion/
precompute.py for the source of that logic.

Key difference from MeanReversion's precompute: the ENTRY TIMEFRAME
itself (1min/3min/5min) is a swept parameter here, so this script
precomputes and saves one file PER (asset, entry timeframe) pair, each
carrying that timeframe's own OHLCV + RSI + ATR columns plus the
(timeframe-independent) daily liquidity-level columns broadcast onto it.

*** DAY BOUNDARY: America/New_York midnight, NOT UTC midnight ***
Deliberately different from MeanReversion's own UTC-date day_id: this
strategy's whole premise is PDH/PDL/PDH2/PDL2 relative to NY trading
days and entries right after the 9:30 NY equity open, so day boundaries
that don't drift against NY local time matter here in a way they didn't
for MeanReversion's fixed intraday fair-value anchor. See README.md's
"Assumptions" section.

*** ASSET UNIVERSE (narrowed 2026-09-19 per Tim) ***
Only 7 assets: EURUSD, GBPUSD, XAUUSD (gold), NDX100, SPX500, US30, AAPL.
A broader 18-asset universe (more forex majors + GER40/FRA40/UK100/
JPN225 + WMT/XOM/DIS) was precomputed first -- see git history if
that's ever worth reviving -- but this file now only defines the 7 in
active use. Confirmed real history depth from the VPS's own
precompute_all.ps1 run (2026-09-19): EURUSD/GBPUSD/US30 ~10.2yr, NDX100
~10.8yr, SPX500 ~10.1yr, AAPL ~9.3yr. XAUUSD is present in both the
local laptop cache and the VPS listing, but its exact history depth on
the VPS hasn't been confirmed by an actual precompute run there yet --
check the printed "~N years" line when it runs.

AAPL is NOT in this laptop's local cache -- its 1-min history lives in
the VPS's Dukascopy cache instead (C:/Users/Administrator/RCTBE/data/
dukascopy), confirmed 2026-09-19 against Tim's own directory listing
(folder name "AAPLUSUSD").

Usage:
    py -3 precompute.py <ASSET>          # all 3 entry timeframes
    py -3 precompute.py <ASSET> <TF>     # just one, e.g. 5min
    py -3 precompute.py ALL
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════
#  Data source + divisor-harmonization — copied from MeanReversion's
#  precompute.py (itself copied from RCTBE), not imported. See module
#  docstring.
# ══════════════════════════════════════════════════════════════════════════

DUKASCOPY_CACHE_ROOT = Path(
    os.environ.get("WS_DUKASCOPY_ROOT", r"C:\Users\leach\OPRrsitomt5\data\dukascopy")
)

NY_TZ = "America/New_York"

# ── Forex majors — Dukascopy folder name == asset name. ──
FOREX_ASSETS = {"EURUSD": "EURUSD", "GBPUSD": "GBPUSD"}
PLAUSIBLE_RANGE_FOREX = {"EURUSD": (0.7, 1.7), "GBPUSD": (0.9, 2.2)}

# ── Metals — same naming convention as forex. ──
METAL_ASSETS = {"XAUUSD": "XAUUSD"}
PLAUSIBLE_RANGE_METAL = {"XAUUSD": (800, 6000)}

# ── Indices — identical mapping to MeanReversion's INDEX_ASSETS. ──
INDEX_ASSETS = {
    "NDX100": "USATECHIDXUSD", "SPX500": "USA500IDXUSD", "US30": "USA30IDXUSD",
}
PLAUSIBLE_RANGE_INDEX = {
    "NDX100": (5_000, 40_000), "SPX500": (1_500, 10_000), "US30": (10_000, 60_000),
}

# -- Stocks -- NOT in the local laptop cache; lives in the VPS's
# Dukascopy cache instead, confirmed 2026-09-19 against Tim's own
# directory listing of C:/Users/Administrator/RCTBE/data/dukascopy
# (folder names there have no dots/slashes, unlike the generic Dukascopy
# CFD naming this file originally guessed -- e.g. "AAPLUSUSD", not
# "AAPL.US/USD").
STOCK_ASSETS = {"AAPL": "AAPLUSUSD"}
PLAUSIBLE_RANGE_STOCK = {"AAPL": (10, 500)}

ALL_ASSETS = {**FOREX_ASSETS, **METAL_ASSETS, **INDEX_ASSETS, **STOCK_ASSETS}
ALL_RANGES = {**PLAUSIBLE_RANGE_FOREX, **PLAUSIBLE_RANGE_METAL, **PLAUSIBLE_RANGE_INDEX, **PLAUSIBLE_RANGE_STOCK}

DIVISOR_BREAK_LOG_THRESHOLD = np.log(5)

ENTRY_TIMEFRAMES = ["1min", "3min", "5min"]
RSI_PERIODS = [7, 14, 21]
ATR_WINDOWS = [14, 20, 30]
SWING_K = 3                 # fractal half-window, daily bars
SWING_LOOKBACK_DAYS = 20    # how many prior trading days to search for a swing beyond PDH/PDL


def _harmonize_divisor_breaks(df: pd.DataFrame, plausible_range: tuple[float, float]) -> pd.DataFrame:
    """Verbatim port of MeanReversion/RCTBE's divisor-break harmonization."""
    close = df["close"]
    log_ret = np.log(close).diff()
    break_mask = log_ret.abs() > DIVISOR_BREAK_LOG_THRESHOLD

    if break_mask.any():
        log_correction = (-log_ret.where(break_mask, 0.0)).cumsum()
        factor = np.exp(log_correction)
        corrected = df.copy()
        for col in ("open", "high", "low", "close"):
            corrected[col] = df[col] * factor
    else:
        corrected = df.copy()

    median_price = corrected["close"].median()
    lo, hi = plausible_range
    if not (lo <= median_price <= hi):
        target = (lo * hi) ** 0.5
        power = round(np.log10(target / median_price))
        corrected[["open", "high", "low", "close"]] *= 10.0**power

    return corrected


def load_1m_ohlcv(duk_dir: Path, plausible_range: tuple[float, float]) -> pd.DataFrame:
    files = sorted(duk_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No cached data at {duk_dir}")
    max_files = os.environ.get("WS_MAX_FILES")   # dev/smoke-test convenience only — unset in production
    if max_files:
        files = files[-int(max_files):]
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = _harmonize_divisor_breaks(df, plausible_range)
    df = df[df["volume"] > 0]   # dead-hour placeholder bars
    return df[["open", "high", "low", "close", "volume"]].copy()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Standard Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    rsi = rsi.where(avg_gain != 0.0, np.where(avg_loss.eq(0.0), 50.0, 0.0))
    return rsi


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window).mean()


def _build_daily_levels(df_1m: pd.DataFrame) -> pd.DataFrame:
    """One row per NY trading day: high/low/PDH/PDL/PDH2/PDL2, session
    anchors, and the nearest confirmed swing high above PDH / swing low
    below PDL within SWING_LOOKBACK_DAYS prior trading days.

    Swing-beyond-PDH/PDL lookup is O(days x lookback) — trivial at daily
    resolution (a handful of thousand days even over 10 years) so a plain
    python loop is used rather than a vectorized trick; correctness over
    cleverness for something this cheap.
    """
    ny_idx = df_1m.index.tz_convert(NY_TZ)
    day_id = ny_idx.date
    df = df_1m.copy()
    df["_day"] = day_id
    df["_ny_time"] = ny_idx.time

    daily_hl = df.groupby("_day").agg(high=("high", "max"), low=("low", "min"))
    daily_hl["open_midnight"] = df.groupby("_day")["open"].first()
    daily_hl["close"] = df.groupby("_day")["close"].last()

    # Session-open anchor: first bar's open at/after 9:30 NY that day (falls
    # back to the midnight open on a day with no bar >= 9:30, e.g. holidays
    # with a partial/odd session).
    from datetime import time as _time
    after_open = df[df["_ny_time"] >= _time(9, 30)]
    session_open = after_open.groupby("_day")["open"].first()
    daily_hl["session_open_930"] = session_open.reindex(daily_hl.index).fillna(daily_hl["open_midnight"])

    daily_hl = daily_hl.sort_index()
    daily_hl["pdh"] = daily_hl["high"].shift(1)
    daily_hl["pdl"] = daily_hl["low"].shift(1)
    daily_hl["pdh2"] = daily_hl["high"].shift(2)
    daily_hl["pdl2"] = daily_hl["low"].shift(2)

    # Confirmed daily fractal swings (causal: shift(K) so only a swing
    # confirmed by K subsequent days is ever exposed).
    K = SWING_K
    is_fh = daily_hl["high"] == daily_hl["high"].rolling(2 * K + 1, center=True).max()
    is_fl = daily_hl["low"] == daily_hl["low"].rolling(2 * K + 1, center=True).min()
    swing_high_confirmed = daily_hl["high"].where(is_fh).shift(K)
    swing_low_confirmed = daily_hl["low"].where(is_fl).shift(K)

    n = len(daily_hl)
    sh_vals = swing_high_confirmed.to_numpy()
    sl_vals = swing_low_confirmed.to_numpy()
    pdh_vals = daily_hl["pdh"].to_numpy()
    pdl_vals = daily_hl["pdl"].to_numpy()
    swing_high_above = np.full(n, np.nan)
    swing_low_below = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i - SWING_LOOKBACK_DAYS)
        if lo >= i:
            continue
        window_sh = sh_vals[lo:i]
        window_sl = sl_vals[lo:i]
        pdh_i, pdl_i = pdh_vals[i], pdl_vals[i]
        if not np.isnan(pdh_i):
            cand = window_sh[window_sh > pdh_i]
            if cand.size:
                swing_high_above[i] = cand.min()
        if not np.isnan(pdl_i):
            cand = window_sl[window_sl < pdl_i]
            if cand.size:
                swing_low_below[i] = cand.max()

    daily_hl["swing_high_above_pdh"] = swing_high_above
    daily_hl["swing_low_below_pdl"] = swing_low_below
    return daily_hl


LEVEL_COLS = ["pdh", "pdl", "pdh2", "pdl2", "swing_high_above_pdh", "swing_low_below_pdl",
              "session_open_930", "open_midnight"]


def _resample(df_1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "1min":
        return df_1m[["open", "high", "low", "close", "volume"]].copy()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df_1m.resample(tf, label="right", closed="left").agg(agg)
    return out.dropna(subset=["open"])


def _tf_swing_columns(bars: pd.DataFrame) -> pd.DataFrame:
    """Confirmed K=3 fractal swing high/low on THIS entry timeframe's own
    bars (not the daily levels above) — used by whale_sweep.py's
    break_of_structure confirmation mode (bos_mode="fractal"). Same
    causal shift(K) convention as the daily levels and as MeanReversion's
    own 1-min swing columns."""
    K = SWING_K
    is_fh = bars["high"] == bars["high"].rolling(2 * K + 1, center=True).max()
    is_fl = bars["low"] == bars["low"].rolling(2 * K + 1, center=True).min()
    swing_high = bars["high"].where(is_fh).shift(K)
    swing_low = bars["low"].where(is_fl).shift(K)
    # ffill so "the most recent confirmed swing at or before bar i" is a
    # simple column lookup rather than a per-bar backward search.
    return pd.DataFrame({
        "tf_swing_high_confirmed": swing_high.ffill(),
        "tf_swing_low_confirmed": swing_low.ffill(),
    }, index=bars.index)


def precompute_asset(asset: str, timeframes: list[str] | None = None) -> None:
    duk_code = ALL_ASSETS[asset]
    plausible_range = ALL_RANGES[asset]
    duk_dir = DUKASCOPY_CACHE_ROOT / duk_code
    print(f"[{asset}] loading 1-min OHLCV from {duk_dir}...", flush=True)
    df_1m = load_1m_ohlcv(duk_dir, plausible_range)
    n_years = (df_1m.index[-1] - df_1m.index[0]).days / 365.25
    print(f"[{asset}] {len(df_1m):,} 1-min bars, {df_1m.index[0]} .. {df_1m.index[-1]} "
          f"(~{n_years:.1f} years)", flush=True)

    daily = _build_daily_levels(df_1m)

    for tf in (timeframes or ENTRY_TIMEFRAMES):
        bars = _resample(df_1m, tf)
        for period in RSI_PERIODS:
            bars[f"rsi_{period}"] = _rsi(bars["close"], period)
        for window in ATR_WINDOWS:
            bars[f"atr_{window}"] = _atr(bars, window)
        bars = bars.join(_tf_swing_columns(bars))

        ny_idx = bars.index.tz_convert(NY_TZ)
        day_id = pd.Series(ny_idx.date, index=bars.index)
        for col in LEVEL_COLS:
            bars[col] = day_id.map(daily[col])
        bars["day_id"] = day_id.astype(str)
        bars["ny_minutes"] = ny_idx.hour * 60 + ny_idx.minute

        required = [f"atr_{w}" for w in ATR_WINDOWS] + ["pdh", "pdl"]
        out = bars.dropna(subset=required).copy()

        # Downcast to float32 before saving -- roughly halves each file's
        # footprint (price/indicator precision loss is negligible at
        # forex/index/stock price scales). day_id stays a string,
        # ny_minutes becomes int32; everything else numeric is float64
        # by construction and safe to downcast.
        float_cols = out.select_dtypes(include="float64").columns
        out[float_cols] = out[float_cols].astype("float32")
        out["ny_minutes"] = out["ny_minutes"].astype("int32")

        out_path = f"ws_precomputed_{asset}_{tf}.parquet"
        tmp_path = out_path + ".tmp"
        # Write-then-rename: a failure mid-write (e.g. disk full, ^C)
        # leaves only a stray .tmp file, never a truncated file at the
        # real path that later silently breaks selftest.py/whale_sweep.py
        # with a cryptic "Parquet magic bytes not found" error -- this is
        # exactly what happened on the VPS's first precompute_all.ps1 run
        # when the disk filled up partway through (see git history).
        try:
            out.to_parquet(tmp_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        os.replace(tmp_path, out_path)
        print(f"[{asset}][{tf}] saved {len(out):,} rows to {out_path} "
              f"({Path(out_path).stat().st_size / 1e6:.1f} MB)", flush=True)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "ALL":
        assets = list(ALL_ASSETS)
        tfs = None
    else:
        assets = [args[0]]
        tfs = [args[1]] if len(args) > 1 else None
    for asset in assets:
        try:
            precompute_asset(asset, tfs)
        except FileNotFoundError as e:
            print(f"[{asset}] SKIPPED — {e}", flush=True)


if __name__ == "__main__":
    main()
