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
Env vars:
  MNQ_RAW_PATH      -- path to the raw continuous-contract parquet
                        (default is the path on Tim's laptop via the
                        device bridge; on a different machine -- e.g.
                        the VPS -- point this at wherever the raw NQ
                        data was copied to there).
  MNQ_HISTORY_YEARS -- slice to the most recent N years AFTER back-
                        adjustment (default: unset = full available
                        history). 2026-09-23: use 6 here to match the
                        6-year window the VPS's CFD precomputes already
                        use, for an apples-to-apples comparison -- NOT
                        the full 10-year raw history (was MNQ_TEST_YEARS,
                        renamed since this is a real setting now, not
                        just a quick-test slice for the RAM-constrained
                        device-bridge VM).
"""
import os

import numpy as np
import pandas as pd

import precompute as pc

RAW_PATH = os.environ.get(
    "MNQ_RAW_PATH",
    "~/mnt/MeanReversion/NASDAQFuturesData/nq_continuous_1min.parquet",
)
ASSET = "MNQ"


def main():
    # ---- resumable cache: back-adjustment + slicing + daily levels are
    # the expensive, memory-heavy steps -- cache them once so this script
    # can be re-invoked run after run (e.g. under a tool that caps each
    # call to ~180s) and pick up where it left off, one entry-timeframe
    # at a time, instead of re-paying that cost on every partial run.
    history_years = os.environ.get("MNQ_HISTORY_YEARS")
    cache_tag = history_years if history_years else "full"
    df1m_cache_path = f"_mnq_df1m_cache_{cache_tag}.parquet"
    daily_cache_path = f"_mnq_daily_cache_{cache_tag}.parquet"

    if os.path.exists(df1m_cache_path) and os.path.exists(daily_cache_path):
        print(f"[{ASSET}] loading cached adjusted+sliced 1-min bars from {df1m_cache_path}", flush=True)
        df_1m = pd.read_parquet(df1m_cache_path)
        daily = pd.read_parquet(daily_cache_path)
        n_years = (df_1m.index[-1] - df_1m.index[0]).days / 365.25
        print(f"[{ASSET}] {len(df_1m):,} 1-min bars (cached), "
              f"{df_1m.index[0]} .. {df_1m.index[-1]} (~{n_years:.1f} years)", flush=True)
    else:
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
        del raw

        # optional recency slice -- e.g. MNQ_HISTORY_YEARS=6 to match the
        # VPS's 6-year CFD precompute window for an apples-to-apples
        # comparison. Also doubles as a quick-correctness-pass lever on the
        # device-bridge VM this sometimes runs under (~2.8GB RAM -- not
        # enough for the full 10yr/3.5M-row history through
        # _build_daily_levels(); use a small value there, leave unset for
        # the real full-history run on a machine with real RAM).
        if history_years:
            cutoff = df_1m.index[-1] - pd.Timedelta(days=float(history_years) * 365.25)
            df_1m = df_1m[df_1m.index >= cutoff].copy()

        n_years = (df_1m.index[-1] - df_1m.index[0]).days / 365.25
        print(f"[{ASSET}] {len(df_1m):,} 1-min bars after volume>0 filter"
              f"{' + MNQ_HISTORY_YEARS=' + history_years if history_years else ''}, "
              f"{df_1m.index[0]} .. {df_1m.index[-1]} (~{n_years:.1f} years)", flush=True)

        daily = pc._build_daily_levels(df_1m)

        df_1m.to_parquet(df1m_cache_path + ".tmp")
        os.replace(df1m_cache_path + ".tmp", df1m_cache_path)
        daily.to_parquet(daily_cache_path + ".tmp")
        os.replace(daily_cache_path + ".tmp", daily_cache_path)
        print(f"[{ASSET}] cached adjusted+sliced bars + daily levels to "
              f"{df1m_cache_path} / {daily_cache_path} for resumed runs", flush=True)

    for tf in pc.ENTRY_TIMEFRAMES:
        out_path_check = f"ws_precomputed_{ASSET}_{tf}.parquet"
        if os.path.exists(out_path_check):
            print(f"[{ASSET}][{tf}] {out_path_check} already exists -- skipping "
                  f"(delete it to force a rebuild)", flush=True)
            continue
        # Cast to float32 as each column is COMPUTED (not after, on the
        # whole frame) and filter with a boolean mask instead of
        # dropna().copy() -- on 1min's ~2.1M-row frame the naive
        # float64-then-copy version peaks high enough to OOM-kill the
        # ~2.8GB device-bridge VM this sometimes runs under; this keeps
        # peak memory roughly half that, with identical output.
        import gc
        bars = pc._resample(df_1m, tf)
        for period in pc.RSI_PERIODS:
            bars[f"rsi_{period}"] = pc._rsi(bars["close"], period).astype("float32")
        for window in pc.ATR_WINDOWS:
            bars[f"atr_{window}"] = pc._atr(bars, window).astype("float32")
        swing = pc._tf_swing_columns(bars)
        for c in swing.columns:
            bars[c] = swing[c].astype("float32")
        del swing
        gc.collect()

        ny_idx = bars.index.tz_convert(pc.NY_TZ)
        day_id = pd.Series(ny_idx.date, index=bars.index)
        for col in pc.LEVEL_COLS:
            bars[col] = day_id.map(daily[col]).astype("float32")
        bars["day_id"] = day_id.astype(str)
        bars["ny_minutes"] = (ny_idx.hour * 60 + ny_idx.minute).astype("int32")
        del ny_idx, day_id
        gc.collect()

        for c in ("open", "high", "low", "close", "volume"):
            if bars[c].dtype == "float64":
                bars[c] = bars[c].astype("float32")
        required = [f"atr_{w}" for w in pc.ATR_WINDOWS] + ["pdh", "pdl"]
        mask = bars[required].notna().all(axis=1)
        out = bars[mask]
        del bars, mask
        gc.collect()

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
