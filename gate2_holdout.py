#!/usr/bin/env python3
"""
WhaleSweep — Gate 2: clean blind OOS holdout.

Ported from ../MeanReversion/gate2_holdout.py, same convention: anchored
walk-forward split, IS = periods 1-3 combined (first 60% of history),
OOS = periods 4-5 combined (last 40%). A candidate PASSES iff, on the
OOS block: PF_OOS >= 1.05, n_trades_OOS >= 30, and degradation_ratio =
(PF_OOS - 1) / (PF_IS - 1) >= 0.40.

Reads the FULL untruncated file via ws.load_precomputed() (never the
search's own IS-only view, ws._search_view() -- see that function's
docstring). Genuinely blind ONLY if the candidate came from a search run
with WS_IS_ONLY=1 (whale_sweep.py's default since 2026-09-18) -- if a
candidate was produced with WS_IS_ONLY=0, periods 4-5 were already
visible to the search's own accept gate, and this becomes a re-score of
bars already used to select it, not a clean blind test. Same caveat,
same fix, as RCTBE's own `_layer2_gate2_holdout.py` and MeanReversion's
`gate2_holdout.py`, both of which this is ported from.

Usage:
    py -3 gate2_holdout.py --asset NDX100 --tf 5min
    py -3 gate2_holdout.py --asset EURUSD --tf 1min --rank 0 1 2
"""
import argparse
import json

import numpy as np

import whale_sweep as ws

PF_OOS_MIN = 1.05
N_TRADES_OOS_MIN = 30
DEGRADATION_MIN = 0.40


def _candidate_params(row: dict) -> dict:
    # session_start_minutes lived here as a bolt-on before 2026-09-19
    # (it wasn't in ws.SPACE, just written onto every row by sample_params()
    # with a fixed value) -- now it's a native swept SPACE key, so
    # list(ws.SPACE.keys()) already covers it.
    keys = list(ws.SPACE.keys())
    p = {k: row.get(k, ws.SPACE.get(k, [None])[0]) for k in keys}
    # "asset" isn't a swept SPACE key, but generate_signals() needs it
    # (COST_TABLE lookup) -- carry it over explicitly (2026-09-20, cost fix).
    p["asset"] = row["asset"]
    return p


def gate2_check(row: dict, df) -> dict:
    p = _candidate_params(row)
    n = len(df)
    bounds = np.linspace(0, n, ws.N_PERIODS + 1).astype(int)
    is_df = df.iloc[bounds[0]:bounds[3]]
    oos_df = df.iloc[bounds[3]:bounds[5]]

    is_res = ws.backtest_signals(ws.generate_signals(is_df, p), is_df)
    oos_res = ws.backtest_signals(ws.generate_signals(oos_df, p), oos_df)
    is_bt, oos_bt = ws.summarize_trades(is_res), ws.summarize_trades(oos_res)

    if is_bt["n_trades"] == 0 or oos_bt["n_trades"] == 0:
        return {"verdict": "INSUFFICIENT_DATA", "is_bt": is_bt, "oos_bt": oos_bt, "degradation": None}

    pf_is, pf_oos, n_oos = is_bt["profit_factor"], oos_bt["profit_factor"], oos_bt["n_trades"]
    degradation = (pf_oos - 1) / (pf_is - 1) if pf_is > 1 else None
    passed = (pf_oos >= PF_OOS_MIN and n_oos >= N_TRADES_OOS_MIN
              and degradation is not None and degradation >= DEGRADATION_MIN)
    return {
        "verdict": "PASS" if passed else "FAIL",
        "pf_is": pf_is, "pf_oos": pf_oos,
        "n_trades_is": is_bt["n_trades"], "n_trades_oos": n_oos,
        "degradation": round(degradation, 3) if degradation is not None else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True)
    ap.add_argument("--tf", required=True, choices=["1min", "3min", "5min"])
    ap.add_argument("--rank", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    args = ap.parse_args()

    with open(args.top_json, encoding="utf-8") as f:
        all_rows = json.load(f)
    rows = [r for r in all_rows if r["asset"] == args.asset and r["entry_timeframe"] == args.tf]
    print(f"{len(rows)} {args.asset}/{args.tf} candidates in {args.top_json}\n")

    df = ws.load_precomputed(args.asset, args.tf)

    n_pass = 0
    for rank in args.rank:
        if rank >= len(rows):
            continue
        row = rows[rank]
        r = gate2_check(row, df)
        n_pass += (r["verdict"] == "PASS")
        print(f"[rank {rank:2d}] score={row['score']:7.3f} orig_min_pf={row['min_profit_factor']:.3f} "
              f"-> {r['verdict']:18s} PF_IS={r.get('pf_is')} PF_OOS={r.get('pf_oos')} "
              f"n_OOS={r.get('n_trades_oos')} degradation={r.get('degradation')}")

    print(f"\n{n_pass}/{min(len(args.rank), len(rows))} checked candidates PASS the OOS holdout re-check.")


if __name__ == "__main__":
    main()
