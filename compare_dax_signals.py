#!/usr/bin/env python3
"""
Part 2 of the DAX CFD-vs-futures comparison: does the STRATEGY see the same
trades on both data sources?

Builds a precomputed FDAX frame (same pipeline as precompute.py /
precompute_mnq.py: _build_daily_levels, _resample, RSI/ATR/swings, levels)
from the Databento download, crops both it and the GER40 CFD precompute to
the common window, then runs EVERY GER40 candidate in
whale_sweep_output/top_strategies.json on both and matches trades.

Cost is held identical on both sides (COST_TABLE["GER40"]) so any
difference is purely from the price data.
"""
import json
import numpy as np
import pandas as pd
import databento as db

import precompute as pc
import whale_sweep as ws
from gate2_holdout import _candidate_params

FUT_PATH = "GER40Futures/xeur-eobi-20260623-20260922.ohlcv-1m.dbn"
EVAL_START = pd.Timestamp("2026-06-25", tz="UTC")   # 2 days warm-up for PDH/ATR
EVAL_END = pd.Timestamp("2026-08-24 23:59", tz="UTC")  # end of laptop CFD data


def fut_1m():
    df = db.DBNStore.from_file(FUT_PATH).to_df(map_symbols=True)
    df = df[df["symbol"].str.startswith("FDAX SI")]
    df["_d"] = df.index.floor("D")
    vol = df.groupby(["_d", "symbol"])["volume"].sum().reset_index()
    front = vol.sort_values("volume").groupby("_d").tail(1).set_index("_d")["symbol"]
    df = df[df["symbol"].values == front.reindex(df["_d"]).values]
    df = df[["open", "high", "low", "close", "volume"]].astype("float64").sort_index()
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC").as_unit("ms")
    df.index.name = "ts"
    return df[df.index <= EVAL_END]


def build_tf(df_1m, daily, tf):
    bars = pc._resample(df_1m, tf)
    for period in pc.RSI_PERIODS:
        bars[f"rsi_{period}"] = pc._rsi(bars["close"], period)
    for window in pc.ATR_WINDOWS:
        bars[f"atr_{window}"] = pc._atr(bars, window)
    swing = pc._tf_swing_columns(bars)
    for c in swing.columns:
        bars[c] = swing[c]
    ny_idx = bars.index.tz_convert(pc.NY_TZ)
    day_id = pd.Series(ny_idx.date, index=bars.index)
    for col in pc.LEVEL_COLS:
        bars[col] = day_id.map(daily[col])
    bars["day_id"] = day_id.astype(str)
    bars["ny_minutes"] = (ny_idx.hour * 60 + ny_idx.minute).astype("int32")
    required = [f"atr_{w}" for w in pc.ATR_WINDOWS] + ["pdh", "pdl"]
    return bars[bars[required].notna().all(axis=1)]


def crop(df):
    return df[(df.index >= EVAL_START) & (df.index <= EVAL_END)]


def trades_with_ts(df, p):
    tr = ws.get_trade_records(df, p)
    for t in tr:
        t["entry_ts"] = df.index[t["entry_idx"]]
    return tr


def match(cfd_tr, fut_tr, tol):
    used = set()
    pairs = []
    for a in cfd_tr:
        best = None
        for k, b in enumerate(fut_tr):
            if k in used or b["direction"] != a["direction"]:
                continue
            dt = abs(b["entry_ts"] - a["entry_ts"])
            if dt <= tol and (best is None or dt < best[0]):
                best = (dt, k)
        if best:
            used.add(best[1])
            pairs.append((a, fut_tr[best[1]]))
    return pairs


def main():
    f1 = fut_1m()
    daily = pc._build_daily_levels(f1)
    cands = [r for r in json.load(open("whale_sweep_output/top_strategies.json")) if r["asset"] == "GER40"]
    tfs = sorted({r["entry_timeframe"] for r in cands})
    fut = {tf: crop(build_tf(f1, daily, tf)) for tf in tfs}
    # Rebuild the CFD side through the SAME pipeline from its 1-min OHLC, so
    # both sources get identical level/indicator logic. (The laptop's
    # ws_precomputed_GER40_1min/5min files predate the 2026-09-19 session-
    # level update and lack asian/london/prev-week columns; 3min has them.)
    c1 = pd.read_parquet("ws_precomputed_GER40_1min.parquet", columns=["open", "high", "low", "close", "volume"])
    c1 = c1[c1.index <= EVAL_END].astype("float64")
    cdaily = pc._build_daily_levels(c1)
    cfd = {tf: crop(build_tf(c1, cdaily, tf)) for tf in tfs}
    for tf in tfs:
        print(f"{tf}: futures bars {len(fut[tf]):,}  cfd bars {len(cfd[tf]):,}")

    rows, all_pairs = [], []
    for i, r in enumerate(cands):
        tf = r["entry_timeframe"]
        p = _candidate_params(r)
        ct, ft = trades_with_ts(cfd[tf], p), trades_with_ts(fut[tf], p)
        tol = pd.Timedelta(tf) * 2
        pairs = match(ct, ft, tol)
        same_out = sum(a["outcome"] == b["outcome"] for a, b in pairs)
        sc, sf = ws.summarize_trades(ct), ws.summarize_trades(ft)
        rows.append({
            "cand": i, "tf": tf, "tp_mode": r["tp_mode"],
            "n_cfd": len(ct), "n_fut": len(ft), "matched": len(pairs),
            "match_%cfd": 100 * len(pairs) / max(len(ct), 1),
            "match_%fut": 100 * len(pairs) / max(len(ft), 1),
            "same_outcome_%": 100 * same_out / max(len(pairs), 1),
            "R_cfd": sc["total_r"], "R_fut": sf["total_r"],
            "PF_cfd": sc["profit_factor"], "PF_fut": sf["profit_factor"],
            "win_cfd": sc["win_rate_pct"], "win_fut": sf["win_rate_pct"],
        })
        for a, b in pairs:
            all_pairs.append({"cand": i, "tf": tf, "dir": a["direction"],
                              "ts_cfd": a["entry_ts"], "ts_fut": b["entry_ts"],
                              "out_cfd": a["outcome"], "out_fut": b["outcome"],
                              "R_cfd": a["r_multiple"], "R_fut": b["r_multiple"]})

    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(t.round(2).to_string(index=False))
    tot = t[["n_cfd", "n_fut", "matched"]].sum()
    print(f"\nALL {len(t)} GER40 candidates: cfd trades {tot.n_cfd}, futures trades {tot.n_fut}, "
          f"matched {tot.matched} ({100*tot.matched/tot.n_cfd:.1f}% of cfd, {100*tot.matched/tot.n_fut:.1f}% of fut)")
    pp = pd.DataFrame(all_pairs)
    if len(pp):
        print(f"Matched trades with the same outcome: {100*(pp.out_cfd == pp.out_fut).mean():.1f}%")
        print("Outcome crosstab (rows = CFD, cols = futures):")
        print(pd.crosstab(pp.out_cfd, pp.out_fut))
        print(f"Median |R_fut - R_cfd| on matched trades: {(pp.R_fut - pp.R_cfd).abs().median():.3f}R")
    for m in ["fixed_rr", "reversal_to_open", "opposite_level"]:
        s = t[t.tp_mode == m]
        if len(s):
            print(f"{m:18s} n={len(s):2d}  median match {s['match_%cfd'].median():.0f}%  "
                  f"sumR cfd {s.R_cfd.sum():.1f} vs fut {s.R_fut.sum():.1f}")
    t.to_csv("compare_dax_signals_by_candidate.csv", index=False)
    pp.to_csv("compare_dax_signals_matched_trades.csv", index=False)


if __name__ == "__main__":
    main()
