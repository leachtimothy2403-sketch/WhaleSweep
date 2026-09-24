#!/usr/bin/env python3
"""
Step 1 of the FundedNext Flex work (2026-09-24): screen every candidate in a
search pool against Flex $100k rules at a grid of risk levels.

In-sample / out-of-sample: the search itself only saw the first 60% of the
loaded history (WS_IS_ONLY, periods 1-3 of 5). Cohorts starting in that
window are "IS", cohorts starting in the last 40% are "OOS" (never seen by
the search). Risk is chosen per candidate on IS only; OOS is reported for
that same risk as the honest check.

Resumable: appends one row per candidate to the output CSV and skips
candidates already in it, so it can be driven in short chunks.

    WS_HISTORY_YEARS=6 py -3 screen_flex.py whale_sweep_output_mnq_6yr/results.csv flex_screen_mnq.csv
    WS_HISTORY_YEARS=6 py -3 screen_flex.py whale_sweep_output_futures/results.csv flex_screen_futures.csv
Options (env): FLEX_MAX_SECONDS (stop after N s, default 0 = no limit),
               FLEX_TIMEFRAMES (default all), FLEX_RISKS (default 500,750,1000,1250,1500),
               FLEX_SHARD (k/n), FLEX_DONE_GLOB, FLEX_TOP_PER_GROUP (best N by score per asset/tf)
"""
import os
import sys
import time

import numpy as np
import pandas as pd

import whale_sweep as ws
import fundednext_flex_rules as fx

# Contract used on FundedNext per asset: (point value in USD, max contracts on Flex $100k)
#  MNQ : $2/pt, 50 micros
#  6E  : sized as M6E micros ($12,500 per 1.00, 50 micros) -- 5 full 6E minis
#        cap out at ~$300 risk on a typical EURUSD stop. NOTE the 6E search
#        used the 6E (mini) cost; M6E is ~2x more expensive per unit.
#  FDXM: EUR 5/pt ~ $5.85 at EURUSD 1.17, assumed to count as a mini (5 max) --
#        Eurex availability on FundedNext is NOT confirmed.
CONTRACTS = {"MNQ": (2.0, 50), "6E": (12_500.0, 50), "FDXM": (5.85, 5)}
RISKS = [int(x) for x in os.environ.get("FLEX_RISKS", "500,750,1000,1250,1500").split(",")]
TFS = os.environ.get("FLEX_TIMEFRAMES")
MAX_S = float(os.environ.get("FLEX_MAX_SECONDS", "0"))


def params_from_row(row):
    p = {k: row[k] for k in ws.SPACE.keys() if k in row.index}
    for k in ws.SPACE.keys():
        if k not in p or (isinstance(p[k], float) and pd.isna(p[k])):
            p[k] = ws.SPACE[k][0]
    for k, v in list(p.items()):          # numpy/pandas scalars -> python
        if hasattr(v, "item"):
            p[k] = v.item()
    p["asset"] = row["asset"]
    return p


def main(pool_path, out_path):
    pool = pd.read_csv(pool_path) if pool_path.endswith(".csv") else pd.read_json(pool_path)
    pool = pool.reset_index().rename(columns={"index": "pool_row"})
    if TFS:
        pool = pool[pool["entry_timeframe"].isin(TFS.split(","))]
    pool = pool[pool["asset"].isin(CONTRACTS)]
    top_n = int(os.environ.get("FLEX_TOP_PER_GROUP", "0"))   # keep best N by search score per (asset, tf)
    if top_n:
        pool = (pool.sort_values("score", ascending=False)
                    .groupby(["asset", "entry_timeframe"], group_keys=False).head(top_n))
    shard = os.environ.get("FLEX_SHARD")          # e.g. "0/2": run every 2nd candidate
    if shard:
        k, n = map(int, shard.split("/"))
        pool = pool[pool["pool_row"] % n == k]
    import glob
    done = set()
    for f in glob.glob(os.environ.get("FLEX_DONE_GLOB", out_path)):   # e.g. "flex_screen_mnq*.csv"
        done |= set(pd.read_csv(f)["pool_row"])
    todo = pool[~pool["pool_row"].isin(done)]
    print(f"{len(pool)} candidates, {len(done)} already screened, {len(todo)} to go", flush=True)
    t0 = time.time()
    for _, row in todo.iterrows():
        if MAX_S and time.time() - t0 > MAX_S:
            print("time budget reached -- rerun to continue", flush=True)
            break
        asset, tf = row["asset"], row["entry_timeframe"]
        df = ws.load_precomputed(asset, tf)
        split = (df.index[0] + (df.index[-1] - df.index[0]) * 0.6).date()
        p = params_from_row(row)
        rec = ws.get_trade_records(df, p)
        pv, mc = CONTRACTS[asset]
        res = {"pool_row": row["pool_row"], "asset": asset, "tf": tf, "tp_mode": p["tp_mode"],
               "search_score": row.get("score"), "search_pf": row.get("profit_factor"),
               "n_trades": len(rec), "split": split}
        best = None
        for risk in RISKS:
            iss = fx.simulate_flex(rec, df, risk, point_value=pv, max_contracts=mc, start_before=split)
            if not iss.get("cohorts"):
                continue
            res[f"is_pass_{risk}"] = iss["pass%"]
            res[f"is_days_{risk}"] = iss["days_med"]
            key = (iss["pass%"], -(iss["days_med"] or 999))
            if best is None or key > best[0]:
                best = (key, risk, iss)
        if best is None:
            continue
        risk, iss = best[1], best[2]
        oos = fx.simulate_flex(rec, df, risk, point_value=pv, max_contracts=mc, start_from=split)
        full = fx.simulate_flex(rec, df, risk, point_value=pv, max_contracts=mc)
        res.update({
            "best_risk": risk,
            "is_pass": iss["pass%"], "is_days_med": iss["days_med"], "is_cohorts": iss["cohorts"],
            "oos_pass": oos.get("pass%"), "oos_fail": oos.get("fail%"), "oos_days_med": oos.get("days_med"),
            "oos_days_p90": oos.get("days_p90"), "oos_cohorts": oos.get("cohorts"),
            "full_pass": full["pass%"], "full_days_med": full["days_med"],
            "target_raised%": full["target_raised%"], "capped%": full["capped%"],
            "avg_contracts": full["avg_contracts"],
        })
        pd.DataFrame([res]).to_csv(out_path, mode="a", header=not os.path.exists(out_path), index=False)
    left = len(pool) - len(pd.read_csv(out_path)) if os.path.exists(out_path) else len(pool)
    print(f"done this run in {time.time()-t0:.0f}s; {left} left", flush=True)


def report(out_path, top=20):
    import glob
    t = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(out_path.replace(".csv", "*.csv")))])
    t = t.drop_duplicates("pool_row")
    pd.set_option("display.width", 250)
    cols = ["pool_row", "asset", "tf", "tp_mode", "search_pf", "n_trades", "best_risk",
            "is_pass", "is_days_med", "oos_pass", "oos_fail", "oos_days_med", "oos_days_p90",
            "full_pass", "full_days_med", "target_raised%", "capped%"]
    print(f"{len(t)} candidates screened")
    print("\nTop by IS pass% (the selection the search/screen is allowed to make):")
    print(t.sort_values(["is_pass", "is_days_med"], ascending=[False, True]).head(top)[cols].to_string(index=False))
    rho = t[["is_pass", "oos_pass"]].corr(method="spearman").iloc[0, 1]
    print(f"\nSpearman(IS pass%, OOS pass%) across all candidates = {rho:.2f}")
    print("\nBy tp_mode / tf (median over candidates):")
    print(t.groupby(["tf", "tp_mode"])[["is_pass", "oos_pass", "oos_days_med", "best_risk"]].median().round(1).to_string())


if __name__ == "__main__":
    if sys.argv[1] == "report":
        report(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 20)
    else:
        main(sys.argv[1], sys.argv[2])
