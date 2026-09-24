#!/usr/bin/env python3
"""
Look-ahead (leakage) audit -- the "truncation test" (2026-09-24).

Idea: a backtest decision taken at time t may only depend on data up to t.
So: rebuild the whole pipeline (daily levels, resample, indicators, signals)
on 1-min data TRUNCATED at t, and compare every signal whose entry bar closed
before t with the same signal from the full-data run. Direction, level, entry,
stop and target must be identical. Any difference = the full run used data
from after t = look-ahead. This checks precompute.py and whale_sweep.py
together, i.e. exactly where the 2026-09-24 session-open/London leak lived.

    WS_HISTORY_YEARS=6 py -3 audit_leakage.py <results.csv> <row> [--cuts 12] [--days 150]
Exit code 1 if any mismatch is found.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

import precompute as pc
import whale_sweep as ws
from compare_dax_signals import build_tf
from screen_flex import params_from_row

SOURCES = {  # asset -> (1-min parquet, session tz)
    "MNQ": ("_mnq_df1m_cache_6.parquet", None),
    "6E": ("_fut_6E_1m_6.5y_cache.parquet", None),
    "MGC": ("_fut_GC_1m_6.5y_cache.parquet", None),
    "MCL": ("_fut_CL_1m_6.5y_cache.parquet", None),
    "6J": ("_fut_6J_1m_6.5y_cache.parquet", None),
    "6JT": ("_fut_6JT_1m_6.5y_cache.parquet", "Asia/Tokyo"),
    "MGCT": ("_fut_MGCT_1m_6.5y_cache.parquet", "Asia/Tokyo"),
}


def signals(df_1m, tf, p):
    daily = pc._build_daily_levels(df_1m)
    bars = build_tf(df_1m, daily, tf)
    sig = ws.generate_signals(bars, p)
    tfd = pd.Timedelta(tf)
    return {bars.index[s["entry_idx"]]: s for s in sig}, tfd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pool"); ap.add_argument("row", type=int)
    ap.add_argument("--cuts", type=int, default=12); ap.add_argument("--days", type=int, default=150)
    a = ap.parse_args()
    row = pd.read_csv(a.pool).iloc[a.row]
    asset, tf = row["asset"], row["entry_timeframe"]
    path, tz = SOURCES[asset]
    if tz:
        pc.NY_TZ = tz
    p = params_from_row(row)
    raw = pd.read_parquet(path)[["open", "high", "low", "close", "volume"]].astype("float64")
    raw.index = pd.DatetimeIndex(raw.index).tz_convert("UTC").as_unit("ms")
    base = raw[raw.index >= raw.index[-1] - pd.Timedelta(days=a.days)]
    full, tfd = signals(base, tf, p)
    ents = sorted(t for t in full if t > base.index[0] + pd.Timedelta(days=a.days // 3))
    rng = np.random.default_rng(0)
    # cut right after entries (worst case: the decision bar has just closed)
    cuts = sorted({(ents[i] + tfd) for i in rng.choice(len(ents), min(a.cuts, len(ents)), replace=False)})
    bad = []
    for cut in cuts:
        part, _ = signals(base[base.index < cut], tf, p)
        for t, s in full.items():
            if t + tfd > cut:
                continue
            q = part.get(t)
            keys = ("direction", "level", "entry", "sl", "tp")
            if q is None or any(not np.isclose(s[k], q[k]) if isinstance(s[k], float) else s[k] != q[k] for k in keys):
                if t + tfd > cut - pd.Timedelta(days=1):       # only report decisions near the cut
                    bad.append({"cut": cut, "entry_ts": t, "full": {k: s[k] for k in keys},
                                "truncated": None if q is None else {k: q[k] for k in keys}})
    print(f"{asset}#{a.row} {tf} {p['tp_mode']} start={p['session_start_minutes']} "
          f"WS_CAUSAL_OPEN={os.environ.get('WS_CAUSAL_OPEN', 'default')}: "
          f"{len(full)} signals, {len(cuts)} cuts, {len(bad)} look-ahead mismatches")
    for b in bad[:5]:
        print("  cut", b["cut"], "entry", b["entry_ts"], "\n    full     ", b["full"], "\n    truncated", b["truncated"])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
