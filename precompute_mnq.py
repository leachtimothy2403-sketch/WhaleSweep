#!/usr/bin/env python3
"""
WhaleSweep -- one-off adapter: precompute MNQ (Micro Nasdaq-100 futures)
from Tim's own downloaded Databento continuous-contract data, reusing
precompute.py's indicator/daily-level/resample internals UNCHANGED (only
the raw-data LOADING step differs from the Dukascopy path, since this
comes from ~/mnt/MeanReversion/NASDAQFuturesData/nq_continuous_1min.
parquet, not a Dukascopy cache).

2026-09-23, per Tim: does the WhaleSweep liquidity-sweep strategy work
on NASDAQ futures (not just the NDX100 CFD), evaluated against a
Tradeify Growth $100k account? First step is getting a clean precomputed
file -- and the raw data needed a real fix first:

RAW DATA IS NOT BACK-ADJUSTED. `source_symbol` shows 40 distinct
contracts spliced together with a straight cut at each roll (no price
adjustment) -- real gaps up to 513 points at some rolls (quarterly,
~40 over the 10-year history). Left as-is, every roll date would look
like a massive fake "liquidity sweep" to generate_signals() and would
corrupt every ATR/PDH/PDL value computed across it. Applying the
standard "Panama" back-adjustment here: walking rolls newest-to-oldest,
each roll's raw open-vs-prior-close jump is accumulated and subtracted
from every bar before that roll -- so the MOST RECENT contract's real
quoted prices are left untouched (what matters most for a recent-window
screen) and older segments are shifted to splice continuously into it.
This changes absolute price levels for older history but preserves
POINT differences within any window that doesn't itself straddle a
roll -- which is all this strategy's PDH/PDL/ATR logic actually uses.

MNQ is a $2/point-value contract, mapped 1:1 in POINTS onto the exact
same price series as full NQ (Databento's raw data IS the NQ price feed
-- see cost table for MNQ's own commission-in-points estimate). Position
sizing/contract-count logic lives in tradeify_challenge_rules.py, not
here -- this script only produces the precomputed bars.

Run from the WhaleSweep repo root:
    py -3 precompute_mnq.py
"""
import numpy as np
import pandas as pd

import precompute as pc

RAW_PATH = "~/mnt/MeanReversion/NASDAQFuturesData/nq_continuous_1min.parquet"
ASSET = "MNQ"


def main():
    import os
    raw = pd.read_parquet(os.path.expanduser(RAW_PATH))
    print(f"[{ASSET}] raw: {len(raw):,} bars, {raw.index[0]} .. {raw.index[-1]}, "
          f"{raw['source_symbol'].nunique()} contracts", flush=True)

    # ---- back-adjustment ----
    chg = raw["source_symbol"] != raw["source_symbol"].shift(1)
    chg.iloc[0] = False  # the very first bar is not a "roll"
    roll_positions = np.flatnonzero(chg.to_numpy())
    prev_close = raw["close"].shift(1)
    jumps = (raw["open"] - prev_close)

    adj = np.zeros(len(raw))
    cumulative = 0.0
    # walk rolls newest -> oldest, accumulate, apply to everything before it
    for pos in roll_positions[::-1]:
        cumulative += float(jumps.iloc[pos])
        adj[:pos] = cumulative
    # adj[i] is how much to SUBTRACT from bar i's prices (jumps were
    # open-of-new-contract minus close-of-old, i.e. positive jump means
    # the new contract quotes higher -- subtracting from the OLDER,
    # already-lower-quoted segment brings it up to splice continuously)
    for col in ("open", "high", "low", "close"):
        raw[col] = raw[col] + adj

    # sanity check: jumps at roll dates should now be ~0
    prev_close_adj = raw["close"].shift(1)
    resid = (raw["open"] - prev_close_adj).iloc[roll_positions].abs()
    print(f"[{ASSET}] back-adjustment residual at {len(roll_positions)} rolls: "
          f"max={resid.max():.3f}, mean={resid.mean():.3f} points", flush=True)

    df_1m = raw[raw["volume"] > 0][["open", "high", "low", "close", "volume"]].copy()

    # optional recency slice -- the device-bridge VM this sometimes runs
    # under has only ~2.8GB RAM, not enough for the full 10yr/3.5M-row
    # history through _build_daily_levels(); set MNQ_TEST_YEARS for a
    # quick correctness pass there, leave unset for the real full-history
    # run on a machine with real RAM (Tim's own laptop, natively).
    test_years = os.environ.get("MNQ_TEST_YEARS")
    if test_years:
        cutoff = df_1m.index[-1] - pd.Timedelta(days=float(test_years) * 365.25)
        df_1m = df_1m[df_1m.index >= cutoff].copy()

    n_years = (df_1m.index[-1] - df_1m.index[0]).days / 365.25
    print(f"[{ASSET}] {len(df_1m):,} 1-min bars after volume>0 filter"
          f"{' + MNQ_TEST_YEARS=' + test_years if test_years else ''}, "
          f"{df_1m.index[0]} .. {df_1m.index[-1]} (~{n_years:.1f} years)", flush=True)

    daily = pc._build_daily_levels(df_1m)

    for tf in pc.ENTRY_TIMEFRAMES:
        bars = pc._resample(df_1m, tf)
        for period in pc.RSI_PERIODS:
            bars[f"rsi_{period}"] = pc._rsi(bars["close"], period)
        for window in pc.ATR_WINDOWS:
            bars[f"atr_{window}"] = pc._atr(bars, window)
        bars = bars.join(pc._tf_swing_columns(bars))

        ny_idx = bars.index.tz_convert(pc.NY_TZ)
        day_id = pd.Series(ny_idx.date, index=bars.index)
        for col in pc.LEVEL_COLS:
            bars[col] = day_id.map(daily[col])
        bars["day_id"] = day_id.astype(str)
        bars["ny_minutes"] = ny_idx.hour * 60 + ny_idx.minute

        required = [f"atr_{w}" for w in pc.ATR_WINDOWS] + ["pdh", "pdl"]
        out = bars.dropna(subset=required).copy()

        float_cols = out.select_dtypes(include="float64").columns
        out[float_cols] = out[float_cols].astype("float32")
        out["ny_minutes"] = out["ny_minutes"].astype("int32")

        out_path = f"ws_precomputed_{ASSET}_{tf}.parquet"
        tmp_path = out_path + ".tmp"
        try:
            out.to_parquet(tmp_path)
        except Exception:
            import pathlib
            pathlib.Path(tmp_path).unlink(missing_ok=True)
            raise
        import os as _os
        _os.replace(tmp_path, out_path)
        print(f"[{ASSET}][{tf}] saved {len(out):,} rows to {out_path}", flush=True)


if __name__ == "__main__":
    main()
