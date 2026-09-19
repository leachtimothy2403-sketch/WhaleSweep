#!/usr/bin/env python3
"""
WhaleSweep -- sweep candidate_report.py's --risk-pct across multiple
levels and see how the worst-24-month-window pass rate moves (2026-09-19,
per Tim: "try to vary the 0.75% risk, and see what the picture looks
like").

Why this is cheap even though it sounds like re-running the whole
screen: gate2_holdout.gate2_check and plateau_check.plateau_check do
NOT take a risk_pct argument at all -- risk only enters at the very
last step, historical_replay_check's FTMO-challenge replay (it scales
each trade's r_multiple into an equity delta). Re-running gate2+plateau
at every risk level would burn most of the compute (plateau_check alone
measured at roughly 9s/candidate vs ~0.4s for the replay check, in this
project's own timing notes) for a result that can't change. So this
script reuses the gate2/plateau verdicts an existing screen_all_
candidates.py run already wrote to candidate_screen.csv, and only
re-runs the one check that actually depends on risk_pct, once per
level.

0.75% wasn't derived for WhaleSweep specifically -- it's the value
RCTBE's PROPFIRM_RANK_RISK_PCT defaults to (and one of several it tests
in _prop_firm_analysis.py's RISK_LEVELS), carried forward through
MeanReversion's candidate_report.py into this project's. This sweep is
how you find out whether 0.75% is actually a good choice for THESE
candidates, rather than trusting the inherited default.

Usage:
    py -3 risk_sweep.py                                      # every candidate in the CSV
    py -3 risk_sweep.py --gate2-pass-only                     # only the ones that already pass Gate 2
    py -3 risk_sweep.py --asset XAUUSD --tf 5min --rank 1     # one specific candidate
    py -3 risk_sweep.py --risk-pcts 0.0025 0.005 0.0075 0.01 0.015 0.02

2026-09-19 addendum (per Tim: "for the various risks discretised by 0.1%
between 0.2 and 2%, what is the pass rate of all the cohorts, and how
long do they take to pass") -- for a handful of hand-picked candidates
at fine risk resolution, --pick switches to a per-candidate detail
table (one row per risk level: overall pass/fail/still-going rate
across ALL cohorts, not just the worst window, plus median/mean days
to pass) instead of the compact single-line-per-candidate view above:

    py -3 risk_sweep.py --pick XAUUSD:5min:1 --pick GER40:5min:2 \
        --risk-min 0.002 --risk-max 0.02 --risk-step 0.001
"""
import argparse
import csv
import json
import time

import whale_sweep as ws
from candidate_report import historical_replay_check

# Mirrors the range RCTBE's own _prop_firm_analysis.py tests
# (RISK_LEVELS = [0.002, 0.0025, ..., 0.02]), trimmed to a handful of
# representative points rather than all eleven.
DEFAULT_RISK_PCTS = [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen-csv", default="whale_sweep_output/candidate_screen.csv",
                     help="Existing screen_all_candidates.py output, reused for gate2/plateau "
                          "verdicts so this script never re-checks those.")
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    ap.add_argument("--asset", default=None)
    ap.add_argument("--tf", default=None, choices=["1min", "3min", "5min"])
    ap.add_argument("--rank", type=int, default=None, help="local_rank; use with --asset and --tf")
    ap.add_argument("--gate2-pass-only", action="store_true")
    ap.add_argument("--pick", action="append", default=None,
                     help="ASSET:TF:RANK, e.g. XAUUSD:5min:1 -- repeat for multiple candidates. "
                          "When given, restricts to exactly these (in this order) and switches to "
                          "a per-candidate detail table (pass/fail/still-going rate across ALL "
                          "cohorts + days-to-pass), not the compact worst-window-only view.")
    ap.add_argument("--risk-pcts", type=float, nargs="+", default=DEFAULT_RISK_PCTS)
    ap.add_argument("--risk-min", type=float, default=None,
                     help="With --risk-max/--risk-step, generates --risk-pcts as an arithmetic "
                          "sequence instead of listing every level by hand.")
    ap.add_argument("--risk-max", type=float, default=None)
    ap.add_argument("--risk-step", type=float, default=None)
    ap.add_argument("--out-csv", default="whale_sweep_output/risk_sweep.csv")
    args = ap.parse_args()

    if args.risk_min is not None or args.risk_max is not None or args.risk_step is not None:
        if args.risk_min is None or args.risk_max is None or args.risk_step is None:
            raise SystemExit("--risk-min/--risk-max/--risk-step must all be given together")
        n_steps = round((args.risk_max - args.risk_min) / args.risk_step)
        args.risk_pcts = [round(args.risk_min + i * args.risk_step, 6) for i in range(n_steps + 1)]

    with open(args.screen_csv, newline="", encoding="utf-8") as f:
        screen_rows = list(csv.DictReader(f))
    with open(args.top_json, encoding="utf-8") as f:
        top_rows = json.load(f)

    # local_rank means the same thing here as everywhere else in this
    # project: position within (asset, tf) in top_strategies.json's own
    # file order. Re-derive it from top_rows rather than trusting the
    # CSV's own local_rank column blindly, but they should always agree
    # unless top_strategies.json was regenerated after the CSV.
    per_key = {}
    for r in top_rows:
        key = (r["asset"], r["entry_timeframe"])
        per_key.setdefault(key, []).append(r)

    targets = []  # (screen_row, top_row, local_rank)
    skipped = 0
    if args.pick:
        # Explicit ASSET:TF:RANK list, in the given order -- look each up
        # in candidate_screen.csv for its gate2/plateau verdict (falling
        # back to "?" if it's not in there, e.g. a rank screen_all_
        # candidates.py was never asked to check) and in top_strategies.json
        # for its actual parameters.
        screen_index = {(sr["asset"], sr["tf"], int(sr["local_rank"])): sr for sr in screen_rows}
        for spec in args.pick:
            try:
                asset, tf, rank_s = spec.split(":")
                lr = int(rank_s)
            except ValueError:
                raise SystemExit(f"--pick expects ASSET:TF:RANK, got {spec!r}")
            key = (asset, tf)
            rows_for_key = per_key.get(key, [])
            if lr >= len(rows_for_key):
                print(f"[!] skipped {spec}: rank {lr} not found in {args.top_json} for {key} "
                      f"(only {len(rows_for_key)} candidate(s) there)")
                skipped += 1
                continue
            sr = screen_index.get((asset, tf, lr), {})
            targets.append((sr, rows_for_key[lr], lr))
    else:
        for sr in screen_rows:
            if args.asset is not None and sr["asset"] != args.asset:
                continue
            if args.tf is not None and sr["tf"] != args.tf:
                continue
            if args.rank is not None and int(sr["local_rank"]) != args.rank:
                continue
            if args.gate2_pass_only and sr.get("gate2_verdict") != "PASS":
                continue
            key = (sr["asset"], sr["tf"])
            lr = int(sr["local_rank"])
            rows_for_key = per_key.get(key, [])
            if lr >= len(rows_for_key):
                skipped += 1
                continue
            targets.append((sr, rows_for_key[lr], lr))

    if skipped:
        print(f"[!] skipped {skipped} row(s) not found in {args.top_json} "
              f"-- candidate_screen.csv is likely stale vs a regenerated top_strategies.json\n")

    print(f"Sweeping risk levels {', '.join(f'{r * 100:.2f}%' for r in args.risk_pcts)} across "
          f"{len(targets)} candidates from {args.screen_csv} "
          f"(gate2/plateau verdicts reused as-is, not re-checked)\n")

    df_cache = {}
    out_rows = []
    t0 = time.time()
    for i, (sr, row, lr) in enumerate(targets):
        key = (row["asset"], row["entry_timeframe"])
        if key not in df_cache:
            df_cache[key] = ws.load_precomputed(*key)
        df = df_cache[key]

        per_risk = {}
        for risk_pct in args.risk_pcts:
            try:
                replay = historical_replay_check(row, df, risk_pct)
            except Exception as e:
                print(f"  [{key} rank={lr} risk={risk_pct}] ERROR: {e}")
                continue
            per_risk[risk_pct] = replay
            out_rows.append({
                "asset": row["asset"], "tf": row["entry_timeframe"], "local_rank": lr,
                "risk_pct": risk_pct,
                "gate2_verdict": sr.get("gate2_verdict"), "plateau_verdict": sr.get("plateau_verdict"),
                "n_cohorts": replay.get("n_cohorts"),
                "n_pass": replay.get("n_pass"), "n_fail": replay.get("n_fail"),
                "overall_pass_pct": replay.get("overall_pass_pct"),
                "overall_fail_pct": replay.get("overall_fail_pct"),
                "overall_still_going_pct": replay.get("overall_still_going_pct"),
                "median_days_to_pass": replay.get("median_days_to_pass"),
                "mean_days_to_pass": replay.get("mean_days_to_pass"),
                "min_days_to_pass": replay.get("min_days_to_pass"),
                "max_days_to_pass": replay.get("max_days_to_pass"),
                "worst_window_pass_pct": replay.get("worst_window_pass_pct"),
                "worst_window_n": replay.get("worst_window_n"),
                "worst_window_start": replay.get("worst_window_start"),
                "note": replay.get("note"),
            })

        if args.pick:
            # Detail mode: one row per risk level, not one line per
            # candidate -- 19 risk levels don't fit on one line legibly.
            g2v = sr.get("gate2_verdict", "?")
            plv = sr.get("plateau_verdict", "?")
            print(f"\n=== {row['asset']} {row['entry_timeframe']} rank={lr} "
                  f"(gate2={g2v}, plateau={plv}) ===")
            print(f"{'risk_pct':>9s} {'pass%':>7s} {'fail%':>7s} {'still_going%':>13s} "
                  f"{'n_cohorts':>9s} {'median_days':>12s} {'mean_days':>10s} {'min-max_days':>13s}")
            for r in args.risk_pcts:
                rep = per_risk.get(r)
                if rep is None:
                    print(f"{r * 100:8.2f}%  ERROR")
                    continue
                if not rep.get("n_cohorts"):
                    note = rep.get("note") or "0 cohorts"
                    print(f"{r * 100:8.2f}%  {note}")
                    continue
                pass_pct = rep.get("overall_pass_pct")
                fail_pct = rep.get("overall_fail_pct")
                sg_pct = rep.get("overall_still_going_pct")
                med = rep.get("median_days_to_pass")
                mean_d = rep.get("mean_days_to_pass")
                mn, mx = rep.get("min_days_to_pass"), rep.get("max_days_to_pass")
                minmax = f"{mn}-{mx}" if mn is not None else "n/a"
                print(f"{r * 100:8.2f}%  {pass_pct:6.1f}% {fail_pct:6.1f}% {sg_pct:12.1f}% "
                      f"{rep.get('n_cohorts'):9d} "
                      f"{(str(med) if med is not None else 'n/a'):>12s} "
                      f"{(str(mean_d) if mean_d is not None else 'n/a'):>10s} {minmax:>13s}")
        elif per_risk:
            g2v = sr.get("gate2_verdict", "?")
            plv = sr.get("plateau_verdict", "?")
            cells = []
            for r in args.risk_pcts:
                if r not in per_risk:
                    cells.append(f"{r * 100:>5.2f}%->  ERR")
                    continue
                wwp = per_risk[r]["worst_window_pass_pct"]
                cells.append(f"{r * 100:>5.2f}%->{wwp:5.1f}%" if wwp is not None else f"{r * 100:>5.2f}%->  N/A")
            print(f"{row['asset']:8s} {row['entry_timeframe']:5s} rank={lr:>2d} "
                  f"gate2={g2v:6s} plateau={plv:6s}  " + "  ".join(cells))

        if not args.pick and ((i + 1) % 10 == 0 or (i + 1) == len(targets)):
            print(f"  ({i + 1}/{len(targets)} candidates, {time.time() - t0:.0f}s elapsed)")

    if out_rows:
        import os
        os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            for r in out_rows:
                w.writerow(r)
        print(f"\nFull long-format results ({len(out_rows)} rows, one per candidate x risk level) "
              f"written to {args.out_csv}")
    if args.pick:
        print("\nNote the still_going% column, not just pass/fail: at low risk most cohorts never "
              "resolve either way within the available history (profit target too far away to "
              "reach), so a 0% pass rate there usually means 'untested', not 'fails'. Read median "
              "vs mean days-to-pass together too -- a mean far above the median means a handful of "
              "slow passes are dragging the average, not a typical outcome.")
    else:
        print("\nRead this for stability, not just the highest number: a candidate whose worst-window "
              "pass rate stays roughly flat across 0.5%-1.5% risk is safer to size than one that only "
              "looks good at exactly one risk level -- that's overfitting to the sweep, the same way a "
              "search candidate that only looks good at one param setting fails the plateau check.")


if __name__ == "__main__":
    main()
