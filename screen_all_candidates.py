#!/usr/bin/env python3
"""
WhaleSweep — screen EVERY merged candidate through the three gates, not
just the highest-scoring few, and re-rank by real-world worthiness
instead of search-time score.

Why this exists (2026-09-19): the first real merged search run's own
results proved the point directly. NDX100/5min's top 3 by score:
  rank 0  score=22.94 (highest)  -> Gate 2 FAIL, worst-24mo window: not
          enough history yet to compute
  rank 1  score=21.70            -> Gate 2 FAIL, worst-24mo window 85.7%
  rank 2  score=20.36 (lowest)   -> Gate 2 FAIL, worst-24mo window 39.0%
          with real max_loss failures
Score order and real prop-firm reliability are NOT the same ordering.
RCTBE hit the identical problem in Layer 2 (see layer2_optimizer.py's
RCTBE_L2_PROPFIRM_RANK: "score doesn't reliably predict real portfolio
value - GBPUSD/USDCAD's actual best candidates scored far below their
pool's top-scored one, which had a NEGATIVE portfolio delta") and fixed
it by ranking on a real prop-firm simulation instead of search-time
score. RCTBE's fix runs INSIDE the search loop (a standalone Monte
Carlo challenge sim per accept-gate survivor, RCTBE_L2_PROPFIRM_RANK).
Re-running WhaleSweep's whole multi-hour search with an equivalent
in-loop change is possible later, but this script is the immediate,
ex-post equivalent: it runs the SAME three checks candidate_report.py
already does (gate2_holdout, plateau_check, real historical-cohort FTMO
replay) against EVERY candidate already sitting in the merged
top_strategies.json - not just rank 0-2 per (asset, tf) - and reports
them sorted by worst-24-month-window pass rate (the number
candidate_report.py's own docstring says is the one worth trusting),
not by score.

This does NOT replace candidate_report.py — once this table points at
a specific candidate worth a closer look, re-run candidate_report.py
with that candidate's own (asset, tf, rank-within-that-pair) to see its
full parameter set and per-check detail. The "local_rank" column here
is exactly the --rank value to pass it.

2026-09-19 addendum: the first real run of this script surfaced a
second, related problem — many DIFFERENT candidates across DIFFERENT
assets shared the exact same worst_window_pass_pct (85.7%, repeatedly).
85.7% = 12/14, and 14 is the smallest cohort count the >=8-cohort
minimum allows to round to that figure — meaning several of those
"good-looking" worst-window numbers rested on as few as ~14 resolved
cohorts, not the multi-year stress-test read the metric is meant to be.
ftmo_challenge_rules.rolling_worst_window_pass_rate now also returns
that window's own cohort count (worst_window_n); this script reports it
as `ww_n` and appends a `*` flag whenever ww_n < 30, so a thin sample
is visible in the table itself rather than silently trusted at face
value alongside a robustly-sampled one.

Usage:
    py -3 screen_all_candidates.py
    py -3 screen_all_candidates.py --asset NDX100 --tf 5min
    py -3 screen_all_candidates.py --risk-pct 0.005 --out-csv my_screen.csv
"""
import argparse
import csv
import json
import os
import time

import whale_sweep as ws
import gate2_holdout as g2
import plateau_check as pc
from candidate_report import historical_replay_check


def screen_one(row: dict, local_rank: int, df, risk_pct: float) -> dict:
    gate2 = g2.gate2_check(row, df)
    plateau = pc.plateau_check(row, df)
    replay = historical_replay_check(row, df, risk_pct)
    return {
        "asset": row["asset"], "tf": row["entry_timeframe"], "local_rank": local_rank,
        "score": row.get("score"), "min_pf": row.get("min_profit_factor"), "n_trades": row.get("n_trades"),
        "gate2_verdict": gate2["verdict"], "pf_is": gate2.get("pf_is"), "pf_oos": gate2.get("pf_oos"),
        "degradation": gate2.get("degradation"),
        "plateau_verdict": plateau["verdict"], "plateau_frac_healthy": plateau["frac_healthy"],
        "n_cohorts": replay.get("n_cohorts"), "overall_pass_pct": replay.get("overall_pass_pct"),
        "overall_fail_pct": replay.get("overall_fail_pct"),
        "worst_window_pass_pct": replay.get("worst_window_pass_pct"),
        "worst_window_start": replay.get("worst_window_start"),
        "worst_window_n": replay.get("worst_window_n"),
        "fail_reasons": replay.get("fail_reasons"),
        "note": replay.get("note"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    ap.add_argument("--asset", default=None, help="Restrict to one asset (default: all)")
    ap.add_argument("--tf", default=None, choices=["1min", "3min", "5min"], help="Restrict to one timeframe")
    ap.add_argument("--risk-pct", type=float, default=0.0075)
    ap.add_argument("--out-csv", default="whale_sweep_output/candidate_screen.csv")
    args = ap.parse_args()

    with open(args.top_json, encoding="utf-8") as f:
        all_rows = json.load(f)

    # local_rank must match candidate_report.py's own --rank indexing:
    # position within just THIS (asset, tf)'s own rows, in file order —
    # computed from the FULL file before any --asset/--tf CLI restriction
    # below, so a filtered run still reports the same rank candidate_
    # report.py would expect.
    per_key_rows = {}
    for r in all_rows:
        key = (r["asset"], r["entry_timeframe"])
        per_key_rows.setdefault(key, []).append(r)

    targets = []  # (row, local_rank)
    for key, rows in per_key_rows.items():
        if args.asset is not None and key[0] != args.asset:
            continue
        if args.tf is not None and key[1] != args.tf:
            continue
        for i, r in enumerate(rows):
            targets.append((r, i))

    print(f"Screening ALL {len(targets)} candidates in {args.top_json} "
          f"(not just the top-scored few per (asset, tf) — score doesn't reliably "
          f"predict prop-firm worthiness, see this script's own docstring)\n")

    t0 = time.time()
    results = []
    df_cache = {}
    for i, (row, local_rank) in enumerate(targets):
        key = (row["asset"], row["entry_timeframe"])
        if key not in df_cache:
            df_cache[key] = ws.load_precomputed(*key)
        df = df_cache[key]
        try:
            results.append(screen_one(row, local_rank, df, args.risk_pct))
        except Exception as e:
            print(f"  [{i + 1}/{len(targets)}] {key} rank={local_rank} ERROR: {e}")
            continue
        if (i + 1) % 10 == 0 or (i + 1) == len(targets):
            print(f"  screened {i + 1}/{len(targets)} ({time.time() - t0:.0f}s elapsed)")

    # Rank by real-world worthiness, not score: Gate 2 PASS first, then by
    # worst-24-month-window pass rate. A candidate with no computable
    # worst-window yet (insufficient history) sorts BELOW any candidate
    # with a real, computed number — proven-good beats unproven, and
    # unproven still beats proven-bad (a low real worst-window number).
    def sort_key(r):
        gate2_ok = 1 if r["gate2_verdict"] == "PASS" else 0
        wwp = r["worst_window_pass_pct"]
        wwn = r["worst_window_n"]
        has_wwp = 1 if wwp is not None else 0
        # A thin worst-window sample (< 30 resolved cohorts, ~7 months)
        # is unproven, not proven-good — don't let its raw percentage
        # outrank a candidate with a robustly-sampled worst window.
        robust = 1 if (wwn is not None and wwn >= 30) else 0
        return (gate2_ok, has_wwp, robust, wwp if wwp is not None else 0)

    results.sort(key=sort_key, reverse=True)

    print(f"\n{'=' * 108}")
    header = (f"{'asset':8s} {'tf':5s} {'rank':4s} {'score':>8s} {'gate2':6s} {'plateau':8s} "
              f"{'worst_win%':>11s} {'ww_n':>5s} {'overall%':>9s} {'n_cohorts':>9s} {'window_start':>12s}")
    print(header)
    for r in results:
        wwp = r["worst_window_pass_pct"]
        wwn = r["worst_window_n"]
        thin_flag = "*" if (wwp is not None and wwn is not None and wwn < 30) else ""
        wwp_str = (f"{wwp:.1f}{thin_flag}" if wwp is not None else
                   ("N/A" if r["note"] is None else "n/trades"))
        wwn_str = str(wwn) if wwn is not None else ""
        overall_str = f"{r['overall_pass_pct']:.1f}" if r["overall_pass_pct"] is not None else "N/A"
        n_cohorts_str = str(r["n_cohorts"]) if r["n_cohorts"] is not None else "0"
        print(f"{r['asset']:8s} {r['tf']:5s} {r['local_rank']:4d} {r['score']:8.2f} {r['gate2_verdict']:6s} "
              f"{r['plateau_verdict']:8s} {wwp_str:>11s} {wwn_str:>5s} {overall_str:>9s} {n_cohorts_str:>9s} "
              f"{str(r['worst_window_start'] or ''):>12s}")

    n_gate2_pass = sum(1 for r in results if r["gate2_verdict"] == "PASS")
    n_both_pass = sum(1 for r in results if r["gate2_verdict"] == "PASS" and r["plateau_verdict"] == "PASS")
    n_real_wwp = sum(1 for r in results if r["worst_window_pass_pct"] is not None)
    n_robust_wwp = sum(1 for r in results if r["worst_window_pass_pct"] is not None
                        and r["worst_window_n"] is not None and r["worst_window_n"] >= 30)
    print(f"\n{n_gate2_pass}/{len(results)} pass Gate 2 (OOS holdout). "
          f"{n_both_pass}/{len(results)} pass BOTH Gate 2 and the plateau check. "
          f"{n_real_wwp}/{len(results)} have SOME worst-24-month-window number, but only "
          f"{n_robust_wwp}/{len(results)} rest on >=30 resolved cohorts (a `*` in the table above "
          f"marks a thin one -- treat those percentages as unproven, not as evidence either way).")
    print("A Gate 2 FAIL doesn't make a candidate automatically worthless (the search's own accept "
          "gate already required min_pf>1 across most walk-forward periods) but its edge didn't hold "
          "up on genuinely unseen data as strongly as its search-time score suggested — weight these "
          "lower even when their worst-window number looks good, and note the search itself was run "
          "with the old WS_IS_ONLY leak if this pool predates that fix (see git history).")

    # 2026-09-19 addendum (per Tim: "lets consider 30%, 50% and 70%"): report
    # cumulative counts at explicit worst-window pass-rate tiers, not just
    # the ranked table above. Reuses the SAME worst_window_pass_pct/
    # worst_window_n this run already computed — no extra checks, no extra
    # cost. Tiers are ">= threshold" and therefore cumulative (a candidate
    # at 85.7% counts toward the 70/50/30 tiers alike), not exclusive
    # buckets. To re-bucket an EXISTING candidate_screen.csv against
    # different tiers without re-running this whole screen, use
    # tier_report.py instead.
    tiers = [70, 50, 30]
    with_wwp = [r for r in results if r["worst_window_pass_pct"] is not None]
    print(f"\nWorst-window pass-rate tiers ({len(with_wwp)}/{len(results)} candidates have a "
          f"computable number):")
    for t in tiers:
        at_or_above = [r for r in with_wwp if r["worst_window_pass_pct"] >= t]
        robust = [r for r in at_or_above
                  if r["worst_window_n"] is not None and r["worst_window_n"] >= 30]
        print(f"  >= {t}%: {len(at_or_above)}/{len(with_wwp)} "
              f"({len(robust)} on a robust >=30-cohort sample)")

    os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
    if results:
        fieldnames = list(results[0].keys())
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in results:
                w.writerow(r)
        print(f"\nFull results (including fail_reasons) written to {args.out_csv}")
    print("Full params for any candidate: py -3 candidate_report.py --asset <A> --tf <TF> "
          "--rank <local_rank from this table>")


if __name__ == "__main__":
    main()
