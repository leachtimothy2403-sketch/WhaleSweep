#!/usr/bin/env python3
"""
Can CFD data stand in for DAX futures? Two robustness ideas, tested on the
2026-06-25 -> 2026-08-24 window where we have both sources.

  margin : minimum sweep margin (sweep_min_margin_atr) and a minimum SL
           buffer, applied identically to CFD and futures. Does CFD<->futures
           agreement improve, and what does it cost in edge?
           usage: python3 dax_robustness_test.py margin <m1,m2,...> <slmin>

  noise  : perturb the CFD 1-min highs/lows with the empirical per-hour
           (futures - basis - CFD) differences measured in
           compare_dax_cfd_bars.parquet, rebuild levels/indicators, rerun
           every candidate. Each run -> _robust/noise_run<k>.csv
           usage: python3 dax_robustness_test.py noise <k_start> <k_end>

  noise_report : summarize the noise runs against the real futures result.
"""
import os
import sys
import json
import numpy as np
import pandas as pd

import precompute as pc
import whale_sweep as ws
from gate2_holdout import _candidate_params
import compare_dax_signals as C

OUT = "_robust"
os.makedirs(OUT, exist_ok=True)


def candidates():
    return [r for r in json.load(open("whale_sweep_output/top_strategies.json")) if r["asset"] == "GER40"]


def cfd_1m():
    c1 = pd.read_parquet("ws_precomputed_GER40_1min.parquet", columns=["open", "high", "low", "close", "volume"])
    return c1[c1.index <= C.EVAL_END].astype("float64")


def frames(df_1m, tfs):
    daily = pc._build_daily_levels(df_1m)
    return {tf: C.crop(C.build_tf(df_1m, daily, tf)) for tf in tfs}


def run_all(cands, fr, overrides=None):
    """-> {cand_idx: trades} using frames fr."""
    out = {}
    for i, r in enumerate(cands):
        p = _candidate_params(r)
        if overrides:
            p.update(overrides(p))
        out[i] = C.trades_with_ts(fr[r["entry_timeframe"]], p)
    return out


def compare(cands, tc, tf_):
    rows = []
    for i, r in enumerate(cands):
        ct, ft = tc[i], tf_[i]
        pairs = C.match(ct, ft, pd.Timedelta(r["entry_timeframe"]) * 2)
        sc, sf = ws.summarize_trades(ct), ws.summarize_trades(ft)
        rows.append({"cand": i, "tf": r["entry_timeframe"], "n_cfd": len(ct), "n_fut": len(ft),
                     "matched": len(pairs), "same_out": sum(a["outcome"] == b["outcome"] for a, b in pairs),
                     "R_cfd": sc["total_r"], "R_fut": sf["total_r"]})
    return pd.DataFrame(rows)


def summarize(t, label):
    def agg(s):
        rel = (s.R_fut - s.R_cfd).abs() / s[["R_cfd", "R_fut"]].abs().max(axis=1).clip(lower=1)
        return {
            "setting": label, "tf": s.tf.iloc[0] if s.tf.nunique() == 1 else "all",
            "trades_cfd": int(s.n_cfd.sum()), "trades_fut": int(s.n_fut.sum()),
            "match%": 100 * s.matched.sum() / max(s.n_cfd.sum(), 1),
            "same_out%": 100 * s.same_out.sum() / max(s.matched.sum(), 1),
            "med_rel_R_gap%": 100 * rel.median(), "worst_rel_R_gap%": 100 * rel.max(),
            "R_cfd": s.R_cfd.sum(), "R_fut": s.R_fut.sum(),
        }
    res = [agg(t)] + [agg(t[t.tf == tf]) for tf in sorted(t.tf.unique())]
    return res


def mode_margin(margins, slmin):
    cands = candidates()
    tfs = sorted({r["entry_timeframe"] for r in cands})
    fc, ff = frames(cfd_1m(), tfs), frames(C.fut_1m(), tfs)
    out = []
    for m in margins:
        ov = lambda p, m=m: {"sweep_min_margin_atr": m, "sl_atr_buffer_mult": max(p["sl_atr_buffer_mult"], slmin)}
        t = compare(cands, run_all(cands, fc, ov), run_all(cands, ff, ov))
        t.to_csv(f"{OUT}/margin_{m}_sl{slmin}.csv", index=False)
        out += summarize(t, f"margin={m}xATR slmin={slmin}")
    r = pd.DataFrame(out)
    pd.set_option("display.width", 250)
    print(r.round(1).to_string(index=False))


def mode_noise(k0, k1):
    cands = candidates()
    tfs = sorted({r["entry_timeframe"] for r in cands})
    bars = pd.read_parquet("compare_dax_cfd_bars.parquet", columns=["d_high", "d_low"])
    hr = bars.index.hour
    pools = {h: (bars.loc[hr == h, "d_high"].to_numpy(), bars.loc[hr == h, "d_low"].to_numpy()) for h in range(24)}
    allp = (bars["d_high"].to_numpy(), bars["d_low"].to_numpy())
    base = cfd_1m()
    hours = base.index.hour
    for k in range(k0, k1):
        rng = np.random.default_rng(1000 + k)
        dh = np.empty(len(base)); dl = np.empty(len(base))
        for h in range(24):
            m = hours == h
            ph, pl = pools[h] if len(pools[h][0]) > 200 else allp
            idx = rng.integers(0, len(ph), m.sum())      # draw (d_high, d_low) as a pair
            dh[m], dl[m] = ph[idx], pl[idx]
        df = base.copy()
        body_hi = df[["open", "close"]].max(axis=1).to_numpy()
        body_lo = df[["open", "close"]].min(axis=1).to_numpy()
        df["high"] = np.maximum(df["high"].to_numpy() + dh, body_hi)
        df["low"] = np.minimum(df["low"].to_numpy() + dl, body_lo)
        tr = run_all(cands, frames(df, tfs))
        rows = [{"cand": i, "run": k, **{kk: v for kk, v in ws.summarize_trades(tr[i]).items()
                                         if kk in ("n_trades", "total_r", "profit_factor", "win_rate_pct")}}
                for i in tr]
        pd.DataFrame(rows).to_csv(f"{OUT}/noise_run{k}.csv", index=False)
        print(f"run {k} done", flush=True)


def mode_noise_report():
    import glob
    runs = pd.concat([pd.read_csv(f) for f in glob.glob(f"{OUT}/noise_run*.csv")])
    base = pd.read_csv("compare_dax_signals_by_candidate.csv")   # clean CFD vs real futures
    g = runs.groupby("cand")["total_r"]
    t = base[["cand", "tf", "tp_mode", "R_cfd", "R_fut"]].merge(
        pd.DataFrame({"noise_p10": g.quantile(.1), "noise_med": g.median(), "noise_p90": g.quantile(.9),
                      "noise_min": g.min(), "noise_max": g.max()}).reset_index(), on="cand")
    t["fut_in_p10_p90"] = (t.R_fut >= t.noise_p10) & (t.R_fut <= t.noise_p90)
    t["fut_in_min_max"] = (t.R_fut >= t.noise_min) & (t.R_fut <= t.noise_max)
    t["noise_spread%"] = 100 * (t.noise_p90 - t.noise_p10) / t.R_cfd.abs().clip(lower=1)
    t["cfd_fut_gap%"] = 100 * (t.R_fut - t.R_cfd).abs() / t.R_cfd.abs().clip(lower=1)
    t["cfd_R_pctile_in_noise"] = [100 * (runs[runs.cand == c].total_r < r).mean() for c, r in zip(t.cand, t.R_cfd)]
    pd.set_option("display.width", 250)
    print(f"{runs.run.nunique()} noise runs")
    print(t.round(1).to_string(index=False))
    print(f"\nfutures result inside noise p10-p90: {100*t.fut_in_p10_p90.mean():.0f}% of candidates; "
          f"inside min-max: {100*t.fut_in_min_max.mean():.0f}%")
    print(f"median noise-run R / clean CFD R: {(t.noise_med / t.R_cfd).median():.2f};  "
          f"real futures R / clean CFD R: {(t.R_fut / t.R_cfd).median():.2f}")
    rho = t[["noise_spread%", "cfd_fut_gap%"]].corr(method="spearman").iloc[0, 1]
    print(f"Spearman(noise spread, actual CFD->futures gap) = {rho:.2f}  "
          f"(positive = noise test flags the candidates that really diverge)")
    t.to_csv(f"{OUT}/noise_report.csv", index=False)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "margin":
        mode_margin([float(x) for x in sys.argv[2].split(",")], float(sys.argv[3]))
    elif mode == "noise":
        mode_noise(int(sys.argv[2]), int(sys.argv[3]))
    elif mode == "noise_report":
        mode_noise_report()
