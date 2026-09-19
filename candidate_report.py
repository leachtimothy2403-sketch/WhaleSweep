#!/usr/bin/env python3
"""
WhaleSweep — one-shot candidate report for a search survivor. Ported
from ../MeanReversion/candidate_report.py, same three-gate structure:

  1. Gate 2 — OOS holdout re-check      (gate2_holdout.gate2_check)
  2. Gate 3 — parameter-plateau check   (plateau_check.plateau_check)
  3. A REAL-historical-replay FTMO check: replay the candidate's actual
     historical trade sequence starting on every calendar Monday
     spanning its trade history, using the real 1-step challenge rules
     (ftmo_challenge_rules.simulate_1step). Reports BOTH the overall
     pass rate (average-case) and the rolling WORST 24-month-window pass
     rate — see ftmo_challenge_rules.py's own docstring for why the
     worst-window number, not the average, is the one worth trusting.
     Same caveats as MeanReversion's version: not a day-block-bootstrap
     Monte Carlo, cohorts overlap their neighbors — a first-pass sanity
     read, not a substitute for a real pooled-portfolio Monte Carlo
     before sizing real risk.

Usage:
    py -3 candidate_report.py --asset NDX100 --tf 5min --rank 0
    py -3 candidate_report.py --asset EURUSD --tf 1min --rank 0 1 2 --risk-pct 0.0075
"""
import argparse
import json
from collections import defaultdict

import whale_sweep as ws
import gate2_holdout as g2
import plateau_check as pc
import ftmo_challenge_rules as ftmo


def historical_replay_check(row: dict, df, risk_pct: float) -> dict:
    p = g2._candidate_params(row)
    records = ws.get_trade_records(df, p)
    if len(records) < 30:
        return {"n_trades": len(records), "note": "too few trades for a meaningful replay"}

    by_date = defaultdict(list)
    for r in records:
        import pandas as pd
        by_date[pd.Timestamp(r["day_id"]).date()].append(r["r_multiple"])
    all_days = sorted(by_date.keys())

    mondays = ftmo.get_mondays_full(all_days)
    outcomes = [ftmo.simulate_1step(dict(by_date), all_days, start, risk_pct) for start in mondays]

    n = len(outcomes)
    n_pass = sum(1 for o in outcomes if o["outcome"] == "PASS")
    n_fail = sum(1 for o in outcomes if o["outcome"] == "FAIL")
    pass_days = [o["days"] for o in outcomes if o["outcome"] == "PASS"]
    fail_reasons = defaultdict(int)
    for o in outcomes:
        if o["outcome"] == "FAIL":
            fail_reasons[o["reason"]] += 1

    outcome_strs = [o["outcome"] for o in outcomes]
    worst_rate, worst_start, worst_n = ftmo.rolling_worst_window_pass_rate(mondays, outcome_strs)

    return {
        "n_trades": len(records), "n_cohorts": n, "n_pass": n_pass, "n_fail": n_fail,
        "overall_pass_pct": round(100 * n_pass / n, 1) if n else None,
        "overall_fail_pct": round(100 * n_fail / n, 1) if n else None,
        "overall_still_going_pct": round(100 * (n - n_pass - n_fail) / n, 1) if n else None,
        "median_days_to_pass": (sorted(pass_days)[len(pass_days) // 2] if pass_days else None),
        # mean alongside median (2026-09-19, per Tim: "how long do they take
        # to pass") -- median alone hides a skewed tail (a handful of very
        # slow passes at low risk can pull the mean far above the median
        # without moving it at all); report both rather than pick one.
        "mean_days_to_pass": (round(sum(pass_days) / len(pass_days), 1) if pass_days else None),
        "min_days_to_pass": (min(pass_days) if pass_days else None),
        "max_days_to_pass": (max(pass_days) if pass_days else None),
        "fail_reasons": dict(fail_reasons),
        "worst_window_pass_pct": round(100 * worst_rate, 1) if worst_start is not None else None,
        "worst_window_start": str(worst_start) if worst_start is not None else None,
        # Sample size the worst-window figure above actually rests on —
        # see rolling_worst_window_pass_rate's own docstring (2026-09-19):
        # an 85.7% resting on 14 cohorts is a much weaker claim than the
        # same number resting on 100+. Treat worst_window_n < ~30 as a
        # thin, noisy read regardless of how good the percentage looks.
        "worst_window_n": worst_n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True)
    ap.add_argument("--tf", required=True, choices=["1min", "3min", "5min"])
    ap.add_argument("--rank", type=int, nargs="+", default=[0])
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    ap.add_argument("--risk-pct", type=float, default=0.0075,
                     help="Risk per trade as a fraction of equity, e.g. 0.0075 = 0.75%%. "
                          "Needs real calibration per candidate, not a default trusted blindly.")
    args = ap.parse_args()

    with open(args.top_json, encoding="utf-8") as f:
        all_rows = json.load(f)
    rows = [r for r in all_rows if r["asset"] == args.asset and r["entry_timeframe"] == args.tf]
    print(f"{len(rows)} {args.asset}/{args.tf} candidates in {args.top_json}\n")

    df = ws.load_precomputed(args.asset, args.tf)

    for rank in args.rank:
        if rank >= len(rows):
            print(f"rank {rank}: out of range")
            continue
        row = rows[rank]
        print(f"{'=' * 70}\n{args.asset}/{args.tf} rank {rank}: score={row['score']} "
              f"min_pf={row['min_profit_factor']} n_trades={row['n_trades']} "
              f"periods_passed={row['periods_passed']}/{row['periods_evaluated']}")
        print({k: row.get(k) for k in ws.SPACE.keys()})

        gate2 = g2.gate2_check(row, df)
        print(f"\n[Gate 2 — OOS holdout re-check]  {gate2['verdict']}  "
              f"PF_IS={gate2.get('pf_is')} PF_OOS={gate2.get('pf_oos')} "
              f"n_OOS={gate2.get('n_trades_oos')} degradation={gate2.get('degradation')}")

        plateau = pc.plateau_check(row, df)
        print(f"[Gate 3 — plateau check]         {plateau['verdict']}  "
              f"{plateau['n_healthy']}/{plateau['n_neighbors']} neighbors healthy "
              f"({plateau['frac_healthy'] * 100:.0f}%)")

        replay = historical_replay_check(row, df, args.risk_pct)
        if "note" in replay:
            print(f"[Historical FTMO replay @ {args.risk_pct*100:.2f}% risk]  {replay['note']} "
                  f"(n_trades={replay['n_trades']})")
        else:
            print(f"[Historical FTMO replay @ {args.risk_pct*100:.2f}% risk]  "
                  f"OVERALL pass={replay['overall_pass_pct']}% fail={replay['overall_fail_pct']}% "
                  f"still_running={replay['overall_still_going_pct']}% "
                  f"median_days_to_pass={replay['median_days_to_pass']} "
                  f"({replay['n_cohorts']} weekly cohorts, {replay['n_trades']} trades) "
                  f"fail_reasons={replay['fail_reasons']}")
            wwp = replay["worst_window_pass_pct"]
            wwn = replay.get("worst_window_n")
            confidence = ("" if wwp is None else
                          "  [THIN SAMPLE -- weight cautiously]" if wwn is not None and wwn < 30 else "")
            print(f"    WORST 24-MONTH WINDOW pass={wwp}%" +
                  (f" (starting {replay['worst_window_start']}, {wwn} resolved cohorts in that window)"
                   if wwp is not None else
                   "  — not enough history yet for a 24-month window with >=8 resolved cohorts") +
                  confidence +
                  "  <-- this, not the overall average above, is the number worth trusting")
        print()


if __name__ == "__main__":
    main()
