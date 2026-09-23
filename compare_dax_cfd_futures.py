#!/usr/bin/env python3
"""
Compare DAX futures (Eurex FDAX, Databento XEUR.EOBI ohlcv-1m) against the
GER40 CFD (Dukascopy, ws_precomputed_GER40_1min.parquet) bar by bar over
their overlapping window.

Part 1: raw candle comparison (basis, per-bar OHLC differences after
        removing the basis, by session).
Output: printed tables + compare_dax_cfd_bars.parquet (aligned bars).
"""
import sys
import numpy as np
import pandas as pd
import databento as db

FUT_PATH = "GER40Futures/xeur-eobi-20260623-20260922.ohlcv-1m.dbn"
CFD_PATH = "ws_precomputed_GER40_1min.parquet"


def load_futures_front():
    df = db.DBNStore.from_file(FUT_PATH).to_df(map_symbols=True)
    df = df[~df["symbol"].str.contains("SPD")]          # drop calendar spreads
    df = df[df["symbol"].str.startswith("FDAX SI")]
    # front month per NY-agnostic UTC date = contract with the most volume that day
    df["_d"] = df.index.floor("D")
    vol = df.groupby(["_d", "symbol"])["volume"].sum().reset_index()
    front = vol.sort_values("volume").groupby("_d").tail(1).set_index("_d")["symbol"]
    df = df[df["symbol"].values == front.reindex(df["_d"]).values]
    return df[["open", "high", "low", "close", "volume", "symbol"]].sort_index()


def main():
    fut = load_futures_front()
    cfd = pd.read_parquet(CFD_PATH, columns=["open", "high", "low", "close"])
    start = max(fut.index.min(), cfd.index.min())
    end = min(fut.index.max(), cfd.index.max())
    fut, cfd = fut.loc[start:end], cfd.loc[start:end]
    print(f"Overlap window: {start} -> {end}")
    print(f"Front contracts used: {fut['symbol'].unique().tolist()}")
    print(f"Bars: futures={len(fut):,}  cfd={len(cfd):,}")

    fut.index = pd.DatetimeIndex(fut.index).tz_convert("UTC").as_unit("ns")
    cfd.index = pd.DatetimeIndex(cfd.index).tz_convert("UTC").as_unit("ns")
    fut = fut.rename(columns={k: k + "_f" for k in ["open", "high", "low", "close", "volume"]})
    cfd = cfd.rename(columns={k: k + "_c" for k in ["open", "high", "low", "close"]})
    j = fut.join(cfd, how="inner")
    assert isinstance(j.index, pd.DatetimeIndex)
    print(f"Common minutes (futures traded AND cfd quoted): {len(j):,} "
          f"({100*len(j)/len(cfd):.1f}% of cfd minutes, {100*len(j)/len(fut):.1f}% of futures minutes)")

    # --- basis: futures minus CFD, daily median over liquid hours ---
    liquid = (j.index.hour >= 7) & (j.index.hour < 15)     # ~Xetra cash hours, UTC summer
    j["_d"] = j.index.floor("D")
    j["basis_raw"] = j["close_f"] - j["close_c"]
    daily_basis = j[liquid].groupby("_d")["basis_raw"].median()
    j["basis"] = daily_basis.reindex(j["_d"]).values
    j = j.dropna(subset=["basis"])
    print("\nBasis (futures - CFD, points), daily median over 07-15 UTC:")
    print(daily_basis.describe().round(1).to_string())
    # intraday basis stability: std of raw close diff within a day, liquid hours
    liq2 = (j.index.hour >= 7) & (j.index.hour < 15)
    intraday_sd = j[liq2].groupby("_d")["basis_raw"].std()
    print(f"Intraday std of (fut close - cfd close) within a day, 07-15 UTC: "
          f"median {intraday_sd.median():.2f} pts, p90 {intraday_sd.quantile(.9):.2f} pts")

    # --- per-bar differences after removing that day's basis ---
    for k in ["open", "high", "low", "close"]:
        j[f"d_{k}"] = (j[f"{k}_f"] - j["basis"]) - j[f"{k}_c"]
    j["range_f"] = j["high_f"] - j["low_f"]
    j["range_c"] = j["high_c"] - j["low_c"]

    h = j.index.hour
    sessions = {
        "Asia 00-06 UTC": (h < 6),
        "EU pre-open 06-07": (h == 6),
        "EU cash 07-13:30": (h >= 7) & (h < 13),
        "EU+US overlap 13-15:30": (h >= 13) & (h < 15),
        "US after EU close 15:30-20": (h >= 15) & (h < 21),
        "ALL": np.ones(len(j), bool),
    }

    def stats(x):
        a = x.abs()
        return pd.Series({
            "n": len(x), "mean": x.mean(), "med|d|": a.median(), "p90|d|": a.quantile(.9),
            "p99|d|": a.quantile(.99), ">=2pt%": 100 * (a >= 2).mean(), ">=5pt%": 100 * (a >= 5).mean(),
        })

    for k in ["high", "low", "close"]:
        print(f"\n{k.upper()} difference (futures - basis - CFD), points:")
        t = pd.DataFrame({s: stats(j.loc[m, f"d_{k}"]) for s, m in sessions.items()}).T
        print(t.round(2).to_string())

    print("\nBar range (high-low), points — median futures vs CFD:")
    t = pd.DataFrame({s: pd.Series({"fut": j.loc[m, "range_f"].median(), "cfd": j.loc[m, "range_c"].median(),
                                     "fut/cfd mean": j.loc[m, "range_f"].mean() / j.loc[m, "range_c"].mean()})
                      for s, m in sessions.items()}).T
    print(t.round(2).to_string())

    # --- daily extremes (what PDH/PDL are built from), NY-day, same as precompute ---
    ny = j.index.tz_convert("America/New_York")
    j["_nyday"] = ny.date
    g = j.groupby("_nyday")
    dd = pd.DataFrame({
        "hi_f": g.apply(lambda x: (x["high_f"] - x["basis"]).max()),
        "hi_c": g["high_c"].max(),
        "lo_f": g.apply(lambda x: (x["low_f"] - x["basis"]).min()),
        "lo_c": g["low_c"].min(),
        "t_hi_f": g.apply(lambda x: (x["high_f"] - x["basis"]).idxmax()),
        "t_hi_c": g["high_c"].idxmax(),
        "t_lo_f": g.apply(lambda x: (x["low_f"] - x["basis"]).idxmin()),
        "t_lo_c": g["low_c"].idxmin(),
    })
    dd["d_hi"] = dd["hi_f"] - dd["hi_c"]
    dd["d_lo"] = dd["lo_f"] - dd["lo_c"]
    dd["same_hi_min"] = (dd["t_hi_f"] - dd["t_hi_c"]).abs() <= pd.Timedelta(minutes=5)
    dd["same_lo_min"] = (dd["t_lo_f"] - dd["t_lo_c"]).abs() <= pd.Timedelta(minutes=5)
    print(f"\nDaily high/low (NY day, common minutes only), {len(dd)} days:")
    print(f"  high diff: median |d| {dd.d_hi.abs().median():.1f} pts, p90 {dd.d_hi.abs().quantile(.9):.1f}, max {dd.d_hi.abs().max():.1f}")
    print(f"  low  diff: median |d| {dd.d_lo.abs().median():.1f} pts, p90 {dd.d_lo.abs().quantile(.9):.1f}, max {dd.d_lo.abs().max():.1f}")
    print(f"  day's high made within 5 min of each other: {100*dd.same_hi_min.mean():.0f}% of days")
    print(f"  day's low  made within 5 min of each other: {100*dd.same_lo_min.mean():.0f}% of days")

    j.drop(columns=["_nyday"]).to_parquet("compare_dax_cfd_bars.parquet")
    dd.to_csv("compare_dax_cfd_daily.csv")


if __name__ == "__main__":
    main()
