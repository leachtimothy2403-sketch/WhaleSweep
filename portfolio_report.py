#!/usr/bin/env python3
"""
WhaleSweep -- portfolio_report.py (2026-09-20, per Tim).

Deep-dive report for ONE already-chosen portfolio combination (as found by
portfolio_optimizer.py): trades/day, the FULL days-to-pass distribution
(not just the median portfolio_optimizer.py prints), and performance
broken out over the last 5 years and last 10 years of available history
(or however much history actually exists -- the printed date range says
exactly how much).

IMPORTANT: this does NOT refit Kelly fractions or the risk multiplier k
against the longer history. It takes the EXACT per-asset risk_pct that
portfolio_optimizer.py already chose (fit on the default HISTORY_YEARS=2.0
window) and asks "how would THIS SAME fixed-risk portfolio have performed
if it had been trading over more history?" -- a robustness check on the
chosen sizing, not a re-optimization. Refitting Kelly on a longer window
would silently change what portfolio you're even looking at.

Run with WS_HISTORY_YEARS=0 (disables the truncation entirely -- loads
everything the precomputed parquet has) to get the longest read possible.
Needs GBPUSD/5min, US30/3min and SPX500/5min precomputed parquets, which
as of 2026-09-19 only exist on the VPS, not the laptop -- run this there.

Usage:
    set WS_HISTORY_YEARS=0            (Windows)  /  export WS_HISTORY_YEARS=0 (bash)
    py -3 portfolio_report.py
"""
import argparse
import json
from collections import defaultdict

import pandas as pd

import whale_sweep as ws
import gate2_holdout as g2
import ftmo_challenge_rules as ftmo

START_EQUITY = ftmo.START_EQUITY

# The exact 6-candidate combo from the 2026-09-19 VPS portfolio_optimizer.py
# run (whale_sweep_output/portfolio_optimizer.csv, size=6 row: avg_corr=0.111,
# pass%=52.3, risk_multiplier_k=0.0209), with each candidate's fixed risk_pct
# as ALREADY chosen there (k * that candidate's own Kelly fraction) -- see
# this file's module docstring for why these are not recomputed here.
DEFAULT_COMBO = [
    {"asset": "GER40",  "tf": "3min", "local_rank": 4, "risk_pct": 1.106},
    {"asset": "EURUSD", "tf": "5min", "local_rank": 6, "risk_pct": 0.962},
    {"asset": "GBPUSD", "tf": "5min", "local_rank": 3, "risk_pct": 0.952},
    {"asset": "XAUUSD", "tf": "5min", "local_rank": 5, "risk_pct": 1.518},
    {"asset": "US30",   "tf": "3min", "local_rank": 7, "risk_pct": 0.717},
    {"asset": "SPX500", "tf": "5min", "local_rank": 5, "risk_pct": 1.105},
]


def _row_for(top_rows, asset, tf, local_rank):
    rows = [r for r in top_rows if r["asset"] == asset and r["entry_timeframe"] == tf]
    if local_rank >= len(rows):
        raise SystemExit(f"local_rank {local_rank} out of range for {asset}/{tf} (only {len(rows)} rows)")
    return rows[local_rank]


def build_trades(row, risk_pct):
    df = ws.load_precomputed(row["asset"], row["entry_timeframe"])
    p = g2._candidate_params(row)
    records = ws.get_trade_records(df, p)
    risk_amt = START_EQUITY * (risk_pct / 100.0)
    trades = [(df.index[r["entry_idx"]], risk_amt * float(r["r_multiple"])) for r in records]
    trades.sort(key=lambda t: t[0])
    return trades, df.index[0], df.index[-1]


def _raw_parquet_range(asset, tf):
    """The precomputed parquet's OWN full date range, read directly and
    bypassing load_precomputed()'s HISTORY_YEARS truncation entirely --
    lets us tell "truncated by the env var" apart from "that's genuinely
    all the history there is" regardless of whether WS_HISTORY_YEARS
    actually took effect in this shell."""
    import os
    path = f"ws_precomputed_{asset}_{tf}.parquet"
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path, columns=[])
    return df.index[0], df.index[-1]


def merge_by_date(all_trades):
    all_entries = [e for trades in all_trades for e in trades]
    all_entries.sort(key=lambda e: e[0])
    by_date = defaultdict(list)
    for ts, val in all_entries:
        by_date[ts.date()].append(val)
    all_days = sorted(by_date.keys())
    return dict(by_date), all_days, len(all_entries)


def cohort_outcomes(by_date, all_days, challenge):
    mondays = ftmo.get_mondays_full(all_days)
    sim_fn = ftmo.simulate_2step_portfolio if challenge == "2step" else ftmo.simulate_1step_portfolio
    return [{"start": start, **sim_fn(by_date, all_days, start)} for start in mondays]


def summarize(outcomes, label):
    n = len(outcomes)
    if n == 0:
        print(f"  {label}: no cohorts")
        return
    n_pass = sum(1 for o in outcomes if o["outcome"] == "PASS")
    n_fail = sum(1 for o in outcomes if o["outcome"] == "FAIL")
    n_going = n - n_pass - n_fail
    pass_days = sorted(o["calendar_days"] for o in outcomes if o["outcome"] == "PASS" and o["calendar_days"])
    print(f"  {label}: n_cohorts={n}  pass={100*n_pass/n:.1f}%  fail={100*n_fail/n:.1f}%  "
          f"still_going={100*n_going/n:.1f}%")
    if pass_days:
        def pct(p):
            idx = min(int(round(p / 100 * (len(pass_days) - 1))), len(pass_days) - 1)
            return pass_days[idx]
        print(f"    days-to-pass among the {len(pass_days)} that passed: "
              f"min={pass_days[0]} p10={pct(10)} p25={pct(25)} median={pct(50)} "
              f"p75={pct(75)} p90={pct(90)} max={pass_days[-1]}")
    else:
        print("    (no cohorts in this window passed)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    ap.add_argument("--challenge", choices=["1step", "2step"], default="2step")
    ap.add_argument("--combo-json", default=None,
                     help="Path to a JSON file with the same structure as DEFAULT_COMBO, to analyze a "
                          "different combination than the current default.")
    args = ap.parse_args()

    combo_spec = DEFAULT_COMBO
    if args.combo_json:
        with open(args.combo_json, encoding="utf-8") as f:
            combo_spec = json.load(f)

    with open(args.top_json, encoding="utf-8") as f:
        top_rows = json.load(f)

    print(f"ws.HISTORY_YEARS in effect: {ws.HISTORY_YEARS} "
          f"(0 = no truncation; if this isn't what you set, check your shell's env-var syntax -- "
          f"PowerShell needs $env:WS_HISTORY_YEARS=\"0\", not set WS_HISTORY_YEARS=0)")

    all_trades, date_ranges, total_trades = [], [], 0
    for spec in combo_spec:
        row = _row_for(top_rows, spec["asset"], spec["tf"], spec["local_rank"])
        try:
            trades, d0, d1 = build_trades(row, spec["risk_pct"])
        except FileNotFoundError:
            print(f"SKIP {spec['asset']}/{spec['tf']} -- precomputed parquet not found here "
                  f"(run this on the VPS for the full combo)")
            continue
        all_trades.append(trades)
        date_ranges.append((d0, d1))
        total_trades += len(trades)
        raw_range = _raw_parquet_range(spec["asset"], spec["tf"])
        raw_note = f", raw parquet range {raw_range[0].date()} -> {raw_range[1].date()}" if raw_range else ""
        print(f"  {spec['asset']}/{spec['tf']} risk_pct={spec['risk_pct']}%: {len(trades)} trades, "
              f"data used {d0.date()} -> {d1.date()}{raw_note}")

    if len(all_trades) < len(combo_spec):
        print(f"\n{len(combo_spec) - len(all_trades)} of {len(combo_spec)} candidates missing here -- "
              f"this report is PARTIAL, not the real combo. Re-run on the VPS for the full answer.")
    if not all_trades:
        raise SystemExit("No candidate data available at all in this environment.")

    by_date, all_days, n_merged = merge_by_date(all_trades)
    span_days = (all_days[-1] - all_days[0]).days + 1
    print(f"\nMerged data span (candidates actually loaded): {all_days[0]} -> {all_days[-1]} "
          f"({span_days} calendar days, {span_days / 365.25:.2f} years)")
    print(f"Total trades merged: {n_merged}")
    print(f"Trades per calendar day (over the full span, incl. weekends/no-trade days): "
          f"{n_merged / span_days:.3f}")
    print(f"Trades per trading day (days with >=1 trade across the whole portfolio, n={len(all_days)}): "
          f"{n_merged / len(all_days):.3f}")

    print(f"\nRunning full-history cohort simulation ({args.challenge}, one cohort per calendar Monday)...")
    outcomes = cohort_outcomes(by_date, all_days, args.challenge)

    print(f"\n=== Full available history ({all_days[0]} -> {all_days[-1]}, "
          f"{span_days / 365.25:.2f} years) ===")
    summarize(outcomes, "all cohorts")

    last_date = all_days[-1]
    for yrs in (5, 10):
        cutoff = last_date - pd.Timedelta(days=int(yrs * 365.25))
        sub = [o for o in outcomes if o["start"] >= cutoff]
        actual_span = (last_date - max(cutoff, all_days[0])).days / 365.25
        note = "" if cutoff >= all_days[0] else f"  (data only goes back to {all_days[0]} -- " \
                                                  f"reporting {actual_span:.2f}y, not the full {yrs}y requested)"
        print(f"\n=== Last {yrs} years (cohorts starting on/after {cutoff}){note} ===")
        summarize(sub, f"last {yrs}y")


if __name__ == "__main__":
    main()
