#!/usr/bin/env python3
"""
WhaleSweep — parameter-plateau check (Gate 3).

Ported from ../MeanReversion/plateau_check.py, same convention: perturb
one swept NUMERIC/ordered parameter at a time by +/-1 grid step (holding
everything else fixed, including asset/entry_timeframe), re-run
backtest_multiperiod, and check whether the neighbor is still "healthy"
(produces a result AND min_profit_factor > 1.0). A candidate whose edge
only exists at one exact, isolated point in parameter space is much more
likely fitted to noise than one whose neighborhood is broadly healthy.

Categorical / non-ordered params (entry_timeframe, confirmation_mode,
bos_mode, rsi_mode, tp_mode, reversal_requires_close,
require_rsi_confirm, sl_extend_to_next_level, include_secondary_levels, allow_level_rearm,
include_session_levels, skip_weekday) are held fixed, not perturbed — flipping any of these
tests a different economic hypothesis, not neighborhood robustness of
this one; the search sweeping them already answers "does another mode
also work".

Usage:
    py -3 plateau_check.py --asset NDX100 --tf 5min --rank 0
"""
import argparse
import json

import whale_sweep as ws

PERTURBABLE = [
    "atr_window", "close_beyond_lookback_bars", "wick_atr_mult",
    "reversal_lookback_bars", "bos_lookback_bars", "bos_confirm_atr_mult",
    "rsi_period", "rsi_ob", "rsi_os", "sl_atr_buffer_mult",
    "sl_extend_check_atr_mult", "rr", "session_start_minutes", "session_end_minutes",
    "max_trades_per_day",
    # cost_atr_mult removed 2026-09-20: no longer a swept SPACE key (real
    # per-asset cost now comes from whale_sweep_cost_table.COST_TABLE
    # instead), so there's nothing left to perturb here.
]


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


def plateau_check(row: dict, df) -> dict:
    base = _candidate_params(row)
    neighbors = []
    for param in PERTURBABLE:
        grid = ws.SPACE[param]
        try:
            idx = grid.index(base[param])
        except ValueError:
            continue
        for step in (-1, 1):
            j = idx + step
            if j < 0 or j >= len(grid):
                continue
            neighbor = dict(base)
            neighbor[param] = grid[j]
            neighbors.append((param, grid[idx], grid[j], neighbor))

    results = []
    for param, old_val, new_val, neighbor in neighbors:
        r = ws.backtest_multiperiod(df, neighbor)
        healthy = r is not None and r["min_profit_factor"] > 1.0
        results.append({
            "param": param, "from": old_val, "to": new_val, "healthy": healthy,
            "min_pf": r["min_profit_factor"] if r else None,
            "periods_passed": f"{r['periods_passed']}/{r['periods_evaluated']}" if r else None,
        })

    n = len(results)
    n_healthy = sum(1 for r in results if r["healthy"])
    frac = n_healthy / n if n else 0.0
    verdict = "PASS" if frac >= 0.70 else ("MIXED" if frac >= 0.40 else "FAIL")
    return {"n_neighbors": n, "n_healthy": n_healthy, "frac_healthy": round(frac, 3),
            "verdict": verdict, "detail": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True)
    ap.add_argument("--tf", required=True, choices=["1min", "3min", "5min"])
    ap.add_argument("--rank", type=int, nargs="+", default=[0])
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    args = ap.parse_args()

    with open(args.top_json, encoding="utf-8") as f:
        all_rows = json.load(f)
    rows = [r for r in all_rows if r["asset"] == args.asset and r["entry_timeframe"] == args.tf]
    print(f"{len(rows)} {args.asset}/{args.tf} candidates in {args.top_json}")

    df = ws.load_precomputed(args.asset, args.tf)

    for rank in args.rank:
        if rank >= len(rows):
            print(f"rank {rank}: out of range")
            continue
        row = rows[rank]
        print(f"\n=== {args.asset}/{args.tf} rank {rank}: score={row['score']} "
              f"min_pf={row['min_profit_factor']} n_trades={row['n_trades']} "
              f"pass={row['periods_passed']}/{row['periods_evaluated']} ===")

        result = plateau_check(row, df)
        print(f"\nPlateau check: {result['n_healthy']}/{result['n_neighbors']} neighbors healthy "
              f"({result['frac_healthy']*100:.0f}%) -> {result['verdict']}")
        for d in result["detail"]:
            status = "OK  " if d["healthy"] else "FAIL"
            print(f"  [{status}] {d['param']:26s} {d['from']} -> {str(d['to']):<8} "
                  f"min_pf={d['min_pf']} periods={d['periods_passed']}")


if __name__ == "__main__":
    main()
