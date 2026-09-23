#!/usr/bin/env python3
"""
WhaleSweep -- one-off: detailed weekly-cohort trace for the size=4 combo
(GER40/3min ss=180 rank9, NDX100/3min ss=180 rank0, EURUSD/3min ss=180
rank5, NDX100/3min ss=420 rank6) at k=0.0677 / margin-cap-frac=0.8 --
the exact configuration portfolio_optimizer.py / sweep_k_tradeoff.py used.

Reuses portfolio_optimizer.py's own margin-gate (_margin_gate) and
ftmo_challenge_rules.py's own day-walker (walk_days) UNCHANGED, so the
accept/reject and pass/fail math is byte-identical to the production
pipeline. What's new here is an INSTRUMENTED phase runner that records
every trade and every day's running balance (run_phase/simulate_2step_
portfolio only return final summary stats), restricted to cohorts
(weekly Monday anchors) starting in the last 3 months of available data.

Run from the WhaleSweep repo root:
    py -3 cohort_report_extract.py
"""
import json
from collections import defaultdict

import pandas as pd

import gate2_holdout as g2
import portfolio_optimizer as po
import whale_sweep as ws
import ftmo_challenge_rules as ftmo
from whale_sweep_leverage_table import leverage_for

START_EQUITY = ftmo.START_EQUITY
K = 0.0677
CAPACITY_FRAC = 0.8
N_MONTHS = 3
OUT_PATH = "cohort_report_data.json"

TARGETS = [
    ("GER40", "3min", 180, 9),
    ("NDX100", "3min", 180, 0),
    ("EURUSD", "3min", 180, 5),
    ("NDX100", "3min", 420, 6),
]

pool = po.load_candidate_pool("whale_sweep_output/candidate_screen.csv", "whale_sweep_output/top_strategies.json")
df_cache = {}
combo = []
direction_by_label_ts = {}
for asset, tf, ss, rank in TARGETS:
    row = next(r for r in pool if r["asset"] == asset and r["entry_timeframe"] == tf
               and r.get("session_start_minutes") == ss and r["local_rank"] == rank)
    cd = po.build_candidate_data(row, df_cache)
    combo.append(cd)
    print(f"{cd['label']}: {cd['n_trades']} raw trades, kelly_f={cd['kelly_f']:.4f}")
    # recover per-trade direction (build_candidate_data's margin_trades doesn't
    # keep it) by re-running the same signal replay and keying on entry_ts
    key = (row["asset"], row["entry_timeframe"])
    p_ = g2._candidate_params(row)
    df_ = df_cache[key]
    for r_ in ws.get_trade_records(df_, p_):
        direction_by_label_ts[(cd["label"], df_.index[r_["entry_idx"]])] = r_["direction"]

# ---- rebuild _merge_trades_with_margin's trade list, but tag asset/label/r_multiple ----
all_trades = []
for c in combo:
    risk_amt = START_EQUITY * (K * c["kelly_f"])
    lev = leverage_for(c["asset"])
    for t in c["margin_trades"]:
        position_size = risk_amt / t["stop_dist"]
        margin = position_size * t["entry_price"] / lev
        all_trades.append({
            "entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
            "dollar_pnl": risk_amt * t["r_multiple"], "margin": margin,
            "asset": c["asset"], "label": c["label"], "r_multiple": t["r_multiple"],
            "entry_price": t["entry_price"], "stop_dist": t["stop_dist"],
            "direction": direction_by_label_ts.get((c["label"], t["entry_ts"])),
        })
all_trades.sort(key=lambda t: t["entry_ts"])
print(f"Total signaled trades across combo: {len(all_trades)}")

capacity = START_EQUITY * CAPACITY_FRAC
kept, n_rejected = po._margin_gate(all_trades, capacity)  # reuse prod logic exactly
print(f"Kept {len(kept)} / rejected {n_rejected} ({100*n_rejected/len(all_trades):.1f}%) at capacity_frac={CAPACITY_FRAC}")

by_date_detail = defaultdict(list)
for t in kept:
    by_date_detail[t["entry_ts"].date()].append(t)
for d in by_date_detail:
    by_date_detail[d].sort(key=lambda t: t["entry_ts"])
all_days = sorted(by_date_detail.keys())

mondays_all = ftmo.get_mondays_full(all_days)
last_day = all_days[-1]
cutoff = last_day - pd.Timedelta(days=int(N_MONTHS * 30.44))
mondays = [m for m in mondays_all if m >= cutoff]
print(f"Data spans {all_days[0]} .. {last_day} ({len(mondays_all)} total weekly cohorts); "
      f"last {N_MONTHS} months -> {len(mondays)} cohorts from {mondays[0]} to {mondays[-1]}")


def run_phase_detailed(days, target_equity, fail_equity, daily_loss_limit, min_days, lockin_scale=None):
    """Mirrors ftmo_challenge_rules.run_phase() EXACTLY (risk_amt=1.0,
    trailing_max_loss=False path, max_concurrent=None, including the
    2026-09-23 lockin_scale tactic -- see its docstring there), but
    returns a full day-by-day / trade-by-trade trace instead of just
    final stats."""
    equity = START_EQUITY
    day_records = []
    outcome, reason, last_idx = "STILL_GOING", None, len(days) - 1
    locked_in = False
    for i, day in enumerate(days):
        day_start_equity = equity
        trades_today = []
        day_outcome = None
        for t in by_date_detail[day]:
            delta = t["dollar_pnl"]
            was_locked_in = locked_in
            if locked_in:
                delta = delta * lockin_scale
            equity += delta
            trades_today.append({
                "asset": t["asset"], "label": t["label"], "direction": t.get("direction"),
                "entry_ts": t["entry_ts"].isoformat(), "exit_ts": t["exit_ts"].isoformat(),
                "r_multiple": t["r_multiple"], "dollar_pnl": delta, "raw_dollar_pnl": t["dollar_pnl"],
                "scaled": was_locked_in, "balance_after": equity,
            })
            days_used = i + 1
            if equity <= fail_equity:
                outcome, reason, last_idx = "FAIL", "max_loss", i
                day_outcome = ("FAIL", "max_loss")
                break
            if equity <= day_start_equity - daily_loss_limit:
                outcome, reason, last_idx = "FAIL", "daily_loss", i
                day_outcome = ("FAIL", "daily_loss")
                break
            if equity >= target_equity and days_used >= min_days:
                outcome, reason, last_idx = "PASS", None, i
                day_outcome = ("PASS", None)
                break
            if lockin_scale is not None and not locked_in and equity >= target_equity:
                locked_in = True
        day_records.append({
            "date": str(day), "day_start_equity": day_start_equity,
            "trades": trades_today, "day_end_equity": equity,
            "day_outcome": day_outcome[0] if day_outcome else None,
            "day_reason": day_outcome[1] if day_outcome else None,
            "locked_in_after": locked_in,
        })
        if day_outcome:
            break
    else:
        outcome, reason, last_idx = "STILL_GOING", None, len(days) - 1
    return {"outcome": outcome, "reason": reason, "last_idx": last_idx,
            "end_equity": equity, "days_used": len(day_records), "day_records": day_records}


def run_cohort(start, lockin_scale=None):
    days = ftmo.walk_days(by_date_detail, all_days, start)
    p1 = run_phase_detailed(days, target_equity=110_000.0, fail_equity=90_000.0,
                             daily_loss_limit=5_000.0, min_days=4, lockin_scale=lockin_scale)
    result = {"cohort_start": str(start), "phase1": p1}
    if p1["outcome"] != "PASS":
        result["outcome"] = p1["outcome"]
        result["reason"] = p1["reason"]
        result["calendar_days"] = (days[p1["last_idx"]] - start).days + 1 if 0 <= p1["last_idx"] < len(days) else None
        result["end_equity"] = p1["end_equity"]
        return result

    phase2_days = days[p1["last_idx"] + 1:]
    if not phase2_days:
        result["outcome"] = "STILL_GOING"
        result["reason"] = None
        result["phase2"] = None
        result["calendar_days"] = None
        result["end_equity"] = p1["end_equity"]
        return result

    p2 = run_phase_detailed(phase2_days, target_equity=105_000.0, fail_equity=90_000.0,
                             daily_loss_limit=5_000.0, min_days=4, lockin_scale=lockin_scale)
    result["phase2"] = p2
    result["outcome"] = p2["outcome"]
    result["reason"] = p2["reason"]
    result["end_equity"] = p2["end_equity"]
    if 0 <= p2["last_idx"] < len(phase2_days):
        result["calendar_days"] = (phase2_days[p2["last_idx"]] - start).days + 1
    else:
        result["calendar_days"] = None
    return result


LOCKIN_SCALE = 0.0  # "lowest possible risk" once a phase target is first exceeded -- see run_phase()'s docstring

cohorts_baseline = [run_cohort(m, lockin_scale=None) for m in mondays]
cohorts_lockin = [run_cohort(m, lockin_scale=LOCKIN_SCALE) for m in mondays]
for cb, cl in zip(cohorts_baseline, cohorts_lockin):
    print(cb["cohort_start"], "baseline:", cb["outcome"], cb.get("reason"), "end_eq=", round(cb["end_equity"], 0),
          "cal_days=", cb.get("calendar_days"), "  |  lockin:", cl["outcome"], cl.get("reason"),
          "end_eq=", round(cl["end_equity"], 0), "cal_days=", cl.get("calendar_days"))

out = {
    "combo_labels": [c["label"] for c in combo],
    "k_used": K, "capacity_frac": CAPACITY_FRAC, "start_equity": START_EQUITY,
    "lockin_scale": LOCKIN_SCALE,
    "n_trades_total": len(all_trades), "n_trades_rejected": n_rejected,
    "data_range": [str(all_days[0]), str(all_days[-1])],
    "n_cohorts_total": len(mondays_all),
    "cohorts_baseline": cohorts_baseline, "cohorts_lockin": cohorts_lockin,
}
with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)
print(f"Wrote {OUT_PATH} ({len(cohorts_baseline)} cohorts x2)")
