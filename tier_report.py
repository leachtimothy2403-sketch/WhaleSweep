#!/usr/bin/env python3
"""
WhaleSweep -- bucket already-screened candidates by worst-24-month-window
pass-rate tiers (2026-09-19, per Tim: "lets consider 30%, 50% and 70%").

This does NOT re-run gate2_holdout / plateau_check / historical_replay_check
-- it reads the CSV screen_all_candidates.py already wrote (whale_sweep_
output/candidate_screen.csv by default) and re-buckets the SAME
worst_window_pass_pct numbers already sitting there. No VPS time, no
re-running the ~35min full screen -- unless top_strategies.json has
changed since that CSV was written, in which case re-run
screen_all_candidates.py first to refresh it.

Tiers are ">= threshold" and therefore CUMULATIVE, not mutually exclusive:
a candidate at 85.7% counts toward the 70%, 50%, AND 30% tiers. That
mirrors how you'd actually use this -- "how many candidates clear my 70%
bar" -- rather than forcing every candidate into exactly one bucket.

Usage:
    py -3 tier_report.py
    py -3 tier_report.py --csv whale_sweep_output/candidate_screen.csv --tiers 30 50 70
    py -3 tier_report.py --min-n 30           # ignore thin (<30 cohort) samples
    py -3 tier_report.py --gate2-pass-only    # only candidates that also passed Gate 2
"""
import argparse
import csv


def _f(s):
    return None if s in (None, "") else float(s)


def _i(s):
    return None if s in (None, "") else int(float(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="whale_sweep_output/candidate_screen.csv")
    ap.add_argument("--tiers", type=float, nargs="+", default=[70, 50, 30])
    ap.add_argument("--min-n", type=int, default=0,
                     help="Only count candidates whose worst_window_n is >= this many resolved "
                          "cohorts. Default 0 = count everything with a computed number, thin "
                          "samples included (marked with * below).")
    ap.add_argument("--gate2-pass-only", action="store_true",
                     help="Only count candidates that also passed Gate 2 (OOS holdout).")
    ap.add_argument("--show", type=int, default=15, help="Max rows to list per tier (0 = all).")
    args = ap.parse_args()

    tiers = sorted(set(args.tiers), reverse=True)

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    usable = []
    for r in rows:
        wwp = _f(r.get("worst_window_pass_pct"))
        wwn = _i(r.get("worst_window_n"))
        if wwp is None:
            continue
        if wwn is not None and wwn < args.min_n:
            continue
        if args.gate2_pass_only and r.get("gate2_verdict") != "PASS":
            continue
        usable.append((r, wwp, wwn))

    filt_note = []
    if args.min_n:
        filt_note.append(f"worst_window_n >= {args.min_n}")
    if args.gate2_pass_only:
        filt_note.append("Gate 2 PASS")
    filt_str = f" ({', '.join(filt_note)})" if filt_note else ""
    print(f"{total} candidates in {args.csv}; {len(usable)} have a computable worst-24-month-window "
          f"pass rate{filt_str}.\n")

    for t in tiers:
        at_or_above = sorted([(r, wwp, wwn) for (r, wwp, wwn) in usable if wwp >= t],
                              key=lambda x: -x[1])
        print(f">= {t:.0f}% worst-window pass rate: {len(at_or_above)}/{len(usable)}")
        limit = len(at_or_above) if args.show == 0 else args.show
        for r, wwp, wwn in at_or_above[:limit]:
            thin = "*" if (wwn is not None and wwn < 30) else ""
            g2v = r.get("gate2_verdict", "?")
            plv = r.get("plateau_verdict", "?")
            print(f"    {r['asset']:8s} {r['tf']:5s} rank={r['local_rank']:>2s}  "
                  f"worst_win={wwp:5.1f}%{thin:1s}  ww_n={str(wwn):>4s}  gate2={g2v:6s} plateau={plv}")
        if len(at_or_above) > limit:
            print(f"    ... and {len(at_or_above) - limit} more (--show 0 for all)")
        print()

    below = [(r, wwp, wwn) for (r, wwp, wwn) in usable if wwp < tiers[-1]]
    print(f"< {tiers[-1]:.0f}%: {len(below)}/{len(usable)}")
    print("\n* = worst_window_n < 30 resolved cohorts -- unproven, not proven-good or proven-bad, "
          "per screen_all_candidates.py's own thin-sample rule.")


if __name__ == "__main__":
    main()
