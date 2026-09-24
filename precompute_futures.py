#!/usr/bin/env python3
"""
WhaleSweep -- precompute futures assets for the futures search (2026-09-24).

Two sources, same downstream pipeline as precompute.py / precompute_mnq.py
(_build_daily_levels, _resample, RSI/ATR/swings, day levels, float32):

  dbn  : a Databento CME/Eurex ohlcv-1m download of a whole product
         (parent symbology, e.g. 6E.FUT). Calendar spreads are dropped,
         the front contract is picked per trading session by volume (never
         rolling back to a contract once left), and rolls are Panama
         back-adjusted (older history shifted to splice onto the newer
         contract; most recent contract's real prices untouched).
             py -3 precompute_futures.py dbn 6E EURUSDFutures\\glbx-mdp3-20160823-20260922.ohlcv-1m.dbn

  cfd  : re-label an existing CFD 1-min precompute as a futures asset
         (e.g. GER40 CFD -> FDXM), rebuilt from its 1-min OHLC so it gets
         the CURRENT level logic (session/prev-week levels) and the
         futures asset's own cost-table entry.
             py -3 precompute_futures.py cfd FDXM ws_precomputed_GER40_1min.parquet

Env:
  FUT_HISTORY_YEARS  slice to the most recent N years before building
                     (default 6.5 = 6-year search window + warm-up)
  FUT_TIMEFRAMES     default "3min,5min" (1min deliberately excluded)
Writes ws_precomputed_<ASSET>_<tf>.parquet; skips files that already exist.
"""
import gc
import os
import sys

import numpy as np
import pandas as pd

import precompute as pc

YEARS = float(os.environ.get("FUT_HISTORY_YEARS", "6.5"))
TFS = [t.strip() for t in os.environ.get("FUT_TIMEFRAMES", "3min,5min").split(",") if t.strip()]


def load_dbn(path):
    import databento as db
    parts = []
    store = db.DBNStore.from_file(path)
    # memory: drop rows older than the history window chunk by chunk (big files, e.g. GC)
    cutoff = (pd.Timestamp(store.end).tz_convert("UTC") - pd.Timedelta(days=YEARS * 365.25 + 30)) if YEARS > 0 else None
    for ch in store.to_df(map_symbols=True, count=1_000_000):
        if cutoff is not None:
            ch = ch[ch.index >= cutoff]
            if ch.empty:
                continue
        ch = ch[~ch["symbol"].str.contains("-") & ~ch["symbol"].str.contains("SPD")]
        parts.append(pd.DataFrame({
            "open": ch["open"].astype("float64"), "high": ch["high"].astype("float64"),
            "low": ch["low"].astype("float64"), "close": ch["close"].astype("float64"),
            "volume": ch["volume"].astype("float64"), "iid": ch["instrument_id"].astype("int64"),
            "symbol": ch["symbol"].astype("category"),
        }, index=ch.index))
    raw = pd.concat(parts).sort_index()
    raw["symbol"] = raw["symbol"].astype(str)
    del parts
    raw.index = pd.DatetimeIndex(raw.index).tz_convert("UTC").as_unit("ms")
    raw.index.name = "ts"
    if YEARS > 0:
        raw = raw[raw.index >= raw.index[-1] - pd.Timedelta(days=YEARS * 365.25 + 30)]

    # trading session = NY date shifted +6h (CME/Eurex evening session belongs to next day)
    sess = (raw.index.tz_convert(pc.NY_TZ) + pd.Timedelta(hours=6)).date
    raw["_s"] = sess
    vol = raw.groupby(["_s", "iid"])["volume"].sum()
    days = sorted(raw["_s"].unique())
    front, retired, cur = {}, set(), None
    for d in days:
        v = vol.loc[d].sort_values(ascending=False)
        cand = next((i for i in v.index if i not in retired), cur)
        if cur is None:
            cur = cand
        elif cand != cur and v.get(cand, 0) > v.get(cur, 0):
            retired.add(cur)
            cur = cand
        front[d] = cur
    raw["_front"] = raw["_s"].map(front)

    # Panama back-adjustment: gap measured on the LAST session before the roll,
    # at the last minute both contracts traded (fallback: new open - old close).
    roll_days = [d for a, d in zip(days[:-1], days[1:]) if front[a] != front[d]]
    by_day = raw.groupby("_s").indices           # session -> row positions
    gaps, day_adj, cum = [], {}, 0.0
    roll_set = set(roll_days)
    for k in range(len(days) - 1, -1, -1):       # newest -> oldest
        d = days[k]
        day_adj[d] = cum                          # shift applied to session d
        if d in roll_set:
            prev = days[k - 1]
            old_id, new_id = front[prev], front[d]
            rows = raw.iloc[by_day[prev]]
            o = rows.loc[rows["iid"] == old_id, "close"]
            n = rows.loc[rows["iid"] == new_id, "close"]
            o = o[~o.index.duplicated()]; n = n[~n.index.duplicated()]
            common = o.index.intersection(n.index)
            if len(common):
                gap = float(n.loc[common[-1]] - o.loc[common[-1]])
            else:
                nd = raw.iloc[by_day[d]]
                gap = float(nd.loc[nd["iid"] == new_id, "open"].iloc[0] - o.iloc[-1])
            gaps.append((d, rows.loc[rows["iid"] == old_id, "symbol"].iloc[0],
                         raw.iloc[by_day[d]].loc[lambda x: x["iid"] == new_id, "symbol"].iloc[0], gap))
            cum += gap                            # everything BEFORE d gets this too
    out = raw[raw["iid"].to_numpy() == raw["_front"].to_numpy()].copy()
    a = out["_s"].map(day_adj).to_numpy(dtype="float64")
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].to_numpy() + a
    # sanity: no duplicate minutes, and the splice at each roll should now be small
    assert not out.index.duplicated().any(), "duplicate timestamps in front-month series"
    print(f"{len(roll_days)} rolls; gaps (newest first):")
    for d, s_old, s_new, g in gaps:
        print(f"  {d} {s_old} -> {s_new}: {g:+.6g}")
    out = out[out["volume"] > 0]
    return out[["open", "high", "low", "close", "volume"]]


def load_cfd(path):
    df = pd.read_parquet(path, columns=["open", "high", "low", "close", "volume"]).astype("float64")
    if YEARS > 0:
        df = df[df.index >= df.index[-1] - pd.Timedelta(days=YEARS * 365.25)]
    return df


def build(asset, df_1m):
    yrs = (df_1m.index[-1] - df_1m.index[0]).days / 365.25
    print(f"[{asset}] {len(df_1m):,} 1-min bars {df_1m.index[0]} .. {df_1m.index[-1]} (~{yrs:.1f}y)", flush=True)
    daily = pc._build_daily_levels(df_1m)
    for tf in TFS:
        path = f"ws_precomputed_{asset}_{tf}.parquet"
        if os.path.exists(path):
            print(f"[{asset}][{tf}] {path} exists -- skipping (delete to rebuild)")
            continue
        bars = pc._resample(df_1m, tf)
        for period in pc.RSI_PERIODS:
            bars[f"rsi_{period}"] = pc._rsi(bars["close"], period).astype("float32")
        for window in pc.ATR_WINDOWS:
            bars[f"atr_{window}"] = pc._atr(bars, window).astype("float32")
        swing = pc._tf_swing_columns(bars)
        for c in swing.columns:
            bars[c] = swing[c].astype("float32")
        del swing
        ny_idx = bars.index.tz_convert(pc.NY_TZ)
        day_id = pd.Series(ny_idx.date, index=bars.index)
        for col in pc.LEVEL_COLS:
            bars[col] = day_id.map(daily[col]).astype("float32")
        bars["day_id"] = day_id.astype(str)
        bars["ny_minutes"] = (ny_idx.hour * 60 + ny_idx.minute).astype("int32")
        for c in ("open", "high", "low", "close", "volume"):
            bars[c] = bars[c].astype("float32")   # same as precompute.py (fine for FX: ~1e-7 precision)
        required = [f"atr_{w}" for w in pc.ATR_WINDOWS] + ["pdh", "pdl"]
        out = bars[bars[required].notna().all(axis=1)]
        out.to_parquet(path + ".tmp")
        os.replace(path + ".tmp", path)
        print(f"[{asset}][{tf}] saved {len(out):,} rows -> {path}  "
              f"(session levels present: {'asian_high' in out.columns})", flush=True)
        del bars, out
        gc.collect()


if __name__ == "__main__":
    mode, asset, src = sys.argv[1], sys.argv[2], sys.argv[3]
    # cache the loaded/back-adjusted 1-min series so a killed/timed-out run
    # can resume without redoing the (slow) DBN load + roll stitching
    cache = f"_fut_{asset}_1m_{YEARS}y_cache.parquet"
    if os.path.exists(cache):
        df = pd.read_parquet(cache)
    else:
        df = load_dbn(src) if mode == "dbn" else load_cfd(src)
        df.to_parquet(cache + ".tmp")
        os.replace(cache + ".tmp", cache)
    if len(sys.argv) > 4 and sys.argv[4] == "--load-only":
        sys.exit(0)
    # Optional session clock (2026-09-24): FUT_TZ=Asia/Tokyo re-anchors the day
    # boundary, session windows (ny_minutes), PDH/PDL and session levels to the
    # given timezone -- e.g. asset 6JT = yen with Tokyo-clock days/sessions.
    # Front-month/roll selection above always stays on the CME (NY) session.
    if os.environ.get("FUT_TZ"):
        pc.NY_TZ = os.environ["FUT_TZ"]
        print(f"[{asset}] session clock: {pc.NY_TZ}", flush=True)
    build(asset, df)
