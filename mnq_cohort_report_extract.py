#!/usr/bin/env python3
"""
WhaleSweep -- one-off: detailed weekly-cohort trace for the single MNQ
futures candidate (5min, fixed_rr, sl_atr_buffer_mult=0.75, rr=1.0,
PF=2.29, win=70.4%) against Tradeify Growth $100k rules, at $1,500 risk
per trade -- the config Tim flagged (2026-09-23) as outperforming the
4-asset CFD portfolio on its own. Mirrors cohort_report_extract.py's
structure/conventions (same day-by-day trace shape, CEST display times)
but for Tradeify's single-phase rules and MNQ's integer-contract sizing
instead of FTMO's 2-phase dollar-risk model.

Restricted to weekly (Monday) cohorts starting in the last 3 months of
the 6-year MNQ data, matching Tim's original "last 3 months" framing for
the CFD combo report.

Run from the WhaleSweep repo root (needs WS_HISTORY_YEARS=6 to see the
full window, and the real ws_precomputed_MNQ_*.parquet files):
    WS_HISTORY_YEARS=6 py -3 mnq_cohort_report_extract.py
"""
import json

import pandas as pd

import whale_sweep as ws
import tradeify_challenge_rules as tr
import ftmo_challenge_rules as ftmo

ASSET, TF = "MNQ", "5min"
PARAMS_OVERRIDE = {
    "sl_atr_buffer_mult": 0.75, "rr": 1.0, "tp_mode": "fixed_rr",
}
RISK_DOLLARS = 1500
N_MONTHS = 3
OUT_PATH = "mnq_cohort_report_data.json"
CEST = "Europe/Berlin"

# rebuild the exact candidate: pull its full param row out of the 6yr
# search results (same one behind the score=15.99 PF=2.29 row reported
# to Tim) rather than hand-assembling defaults, so every other knob
# (confirmation_mode, rsi settings, session hours, etc.) matches exactly.
results = pd.read_csv("whale_sweep_output_mnq_6yr/results.csv")
fixed = results[(results["tp_mode"] == "fixed_rr") & (results["entry_timeframe"] == TF)]
row = fixed[(fixed["sl_atr_buffer_mult"] == PARAMS_OVERRIDE["sl_atr_buffer_mult"])
            & (fixed["rr"] == PARAMS_OVERRIDE["rr"])].sort_values("score", ascending=False).iloc[0]
p = {k: row[k] for k in ws.SPACE.keys() if k in row.index}
for k in ws.SPACE.keys():
    if k not in p or (isinstance(p[k], float) and pd.isna(p[k])):
        p[k] = ws.SPACE[k][0]
p["asset"] = ASSET
print(f"Candidate: {ASSET}/{TF} score={row['score']:.2f} PF={row['profit_factor']:.2f} "
      f"win%={row['win_rate_pct']:.1f} n_trades(6yr)={row['n_trades']}")
print(f"Params: tp_mode={p['tp_mode']} rr={p['rr']} sl_atr_buffer_mult={p['sl_atr_buffer_mult']} "
      f"confirmation_mode={p['confirmation_mode']} session_start={p['session_start_minutes']} "
      f"session_end={p['session_end_minutes']}")

df = ws.load_precomputed(ASSET, TF)
records = ws.get_trade_records(df, p)
print(f"Total trades over full local window ({df.index[0]} .. {df.index[-1]}): {len(records)}")

by_date_detail = {}
for r in records:
    entry_ts = df.index[r["entry_idx"]]
    exit_ts = df.index[r["exit_idx"]]
    risk_points = r["risk"]
    contracts = max(1, min(tr.MAX_CONTRACTS_MNQ, int(round(RISK_DOLLARS / (risk_points * tr.POINT_VALUE_MNQ)))))
    dollar_pnl = contracts * r["r_multiple"] * risk_points * tr.POINT_VALUE_MNQ
    day = entry_ts.date()
    by_date_detail.setdefault(day, []).append({
        "entry_ts": entry_ts, "exit_ts": exit_ts, "direction": r["direction"], "level": r["level"],
        "entry_price": float(r["entry"]), "sl": float(r["sl"]), "tp": float(r["tp"]),
        "risk_points": float(risk_points), "outcome": r["outcome"], "exit_price": float(r["exit_price"]),
        "r_multiple": float(r["r_multiple"]), "contracts": contracts, "dollar_pnl": float(dollar_pnl),
    })
for d in by_date_detail:
    by_date_detail[d].sort(key=lambda t: t["entry_ts"])
all_days = sorted(by_date_detail.keys())

mondays_all = ftmo.get_mondays_full(all_days)
last_day = all_days[-1]
cutoff = last_day - pd.Timedelta(days=int(N_MONTHS * 30.44))
mondays = [m for m in mondays_all if m >= cutoff]
print(f"Data spans {all_days[0]} .. {last_day} ({len(mondays_all)} total weekly cohorts); "
      f"last {N_MONTHS} months -> {len(mondays)} cohorts from {mondays[0]} to {mondays[-1]}")


def run_growth_detailed(days):
    """Mirrors tradeify_challenge_rules.simulate_growth()'s single-phase
    walk EXACTLY (trailing_max_loss=True, risk_amt=1.0 since dollar_pnl
    is already fully sized), but returns a full day-by-day trace."""
    equity = tr.START_EQUITY
    high_water_mark = tr.START_EQUITY
    day_records = []
    outcome, reason, last_idx = "STILL_GOING", None, len(days) - 1
    for i, day in enumerate(days):
        day_start_equity = equity
        high_water_mark = max(high_water_mark, day_start_equity)
        floor = high_water_mark - tr.MAX_DRAWDOWN
        trades_today = []
        day_outcome = None
        for t in by_date_detail[day]:
            equity += t["dollar_pnl"]
            trades_today.append({
                "direction": t["direction"], "level": t["level"],
                "entry_ts_cest": t["entry_ts"].tz_convert(CEST).isoformat(),
                "exit_ts_cest": t["exit_ts"].tz_convert(CEST).isoformat(),
                "entry_price": t["entry_price"], "sl": t["sl"], "tp": t["tp"],
                "risk_points": t["risk_points"], "outcome": t["outcome"], "exit_price": t["exit_price"],
                "r_multiple": t["r_multiple"], "contracts": t["contracts"], "dollar_pnl": t["dollar_pnl"],
                "balance_after": equity,
            })
            days_used = i + 1
            if equity <= floor:
                outcome, reason, last_idx = "FAIL", "max_loss", i
                day_outcome = ("FAIL", "max_loss")
                break
            if equity <= day_start_equity - tr.DAILY_LOSS_LIMIT:
                outcome, reason, last_idx = "FAIL", "daily_loss", i
                day_outcome = ("FAIL", "daily_loss")
                break
            if equity >= tr.TARGET_EQUITY and days_used >= tr.MIN_DAYS:
                outcome, reason, last_idx = "PASS", None, i
                day_outcome = ("PASS", None)
                break
        day_records.append({
            "date": str(day), "day_start_equity": day_start_equity, "trades": trades_today,
            "day_end_equity": equity, "floor": floor,
            "day_outcome": day_outcome[0] if day_outcome else None,
            "day_reason": day_outcome[1] if day_outcome else None,
        })
        if day_outcome:
            break
    else:
        outcome, reason, last_idx = "STILL_GOING", None, len(days) - 1
    return {"outcome": outcome, "reason": reason, "last_idx": last_idx, "end_equity": equity,
            "days_used": len(day_records), "day_records": day_records}


def run_cohort(start):
    days = ftmo.walk_days(by_date_detail, all_days, start)
    result = run_growth_detailed(days)
    calendar_days = ((days[result["last_idx"]] - start).days + 1
                      if 0 <= result["last_idx"] < len(days) else None)
    return {"cohort_start": str(start), "outcome": result["outcome"], "reason": result["reason"],
            "calendar_days": calendar_days, "end_equity": result["end_equity"],
            "day_records": result["day_records"]}


cohorts_out = [run_cohort(m) for m in mondays]
for co in cohorts_out:
    print(co["cohort_start"], co["outcome"], co.get("reason"), "end_eq=", round(co["end_equity"], 0),
          "cal_days=", co.get("calendar_days"))

out = {
    "asset": ASSET, "tf": TF, "candidate_label": f"{ASSET}/{TF} fixed_rr sl={p['sl_atr_buffer_mult']} rr={p['rr']}",
    "profit_factor": float(row["profit_factor"]), "win_rate_pct": float(row["win_rate_pct"]),
    "n_trades_6yr": int(row["n_trades"]), "risk_dollars": RISK_DOLLARS,
    "start_equity": tr.START_EQUITY, "target_equity": tr.TARGET_EQUITY,
    "max_drawdown": tr.MAX_DRAWDOWN, "daily_loss_limit": tr.DAILY_LOSS_LIMIT,
    "min_days": tr.MIN_DAYS, "max_contracts": tr.MAX_CONTRACTS_MNQ,
    "data_range": [str(all_days[0]), str(last_day)],
    "n_cohorts_total": len(mondays_all), "cohorts": cohorts_out,
}
with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1, default=str)
print(f"\nWrote {OUT_PATH} ({len(cohorts_out)} cohorts)")
