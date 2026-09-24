#!/usr/bin/env python3
"""
Weekly-cohort report (last 3 months) for 6E #5374 alone on FundedNext Flex $100k
evaluation rules, $500 risk/trade, 6E minis (max 5), no day stop.
Event-accurate like combo_flex.py: trades realized at their real exit time.
Writes flex_5374_cohorts_last3months.html (self-contained).
    WS_HISTORY_YEARS=6 py -3 cohort_report_5374.py
"""
import json

import numpy as np
import pandas as pd

import whale_sweep as ws
import ftmo_challenge_rules as ftmo
from screen_flex import params_from_row

ROW, RISK, PV, MAX_C = 5374, 500.0, 125_000.0, 5
START, MLL, LOCK, TARGET, CONS = 100_000.0, 2_500.0, 100_100.0, 5_000.0, 0.40
TZ = "Europe/Paris"
N_WEEKS = 13
OUT = "flex_5374_cohorts_last3months.html"


def trades():
    row = pd.read_csv("whale_sweep_output_futures/results.csv").iloc[ROW]
    df = ws.load_precomputed("6E", row["entry_timeframe"])
    p = params_from_row(row)
    sig = {s["entry_idx"]: s for s in ws.generate_signals(df, p)}
    out = []
    for r in ws.backtest_signals(list(sig.values()), df):
        n = max(1, int(round(RISK / (r["risk"] * PV))))
        n_cap = min(n, MAX_C)
        out.append({
            "t_in": df.index[r["entry_idx"]], "t_out": df.index[r["exit_idx"]],
            "direction": r["direction"], "level": r["level"], "entry": r["entry"], "sl": r["sl"], "tp": r["tp"],
            "exit": r["exit_price"], "outcome": r["outcome"], "r": r["r_multiple"], "risk_pts": r["risk"],
            "contracts": n_cap, "wanted": n,
        })
    return row, df, sorted(out, key=lambda x: x["t_in"])


def day_events(day_trades):
    """Realize in exit order; shared 5-contract cap across overlapping trades."""
    open_, done = [], []
    for t in day_trades:
        still = []
        for o in sorted(open_, key=lambda x: x["t_out"]):
            (done if o["t_out"] <= t["t_in"] else still).append(o)
        open_ = still
        free = MAX_C - sum(o["contracts"] for o in open_)
        n = min(t["contracts"], free)
        if n <= 0:
            done.append({**t, "contracts": 0, "pnl": 0.0, "skipped": True, "t_out": t["t_in"]})
            continue
        open_.append({**t, "contracts": n, "pnl": n * t["r"] * t["risk_pts"] * PV, "skipped": False})
    done += open_
    return sorted(done, key=lambda x: x["t_out"])


def run_cohort(by_day, days, start):
    bal, eod_high, best_day = START, START, 0.0
    recs = []
    for i, d in enumerate(days):
        floor = min(eod_high - MLL, LOCK)
        ds = bal
        rec = {"date": str(d), "day_start": ds, "floor": floor, "trades": [], "outcome": None}
        for e in day_events(by_day[d]):
            if not e["skipped"]:
                bal += e["pnl"]
            need = max(TARGET, max(best_day, bal - ds) / CONS)
            rec["trades"].append({
                "entry_t": e["t_in"].tz_convert(TZ).isoformat(), "exit_t": e["t_out"].tz_convert(TZ).isoformat(),
                "dir": e["direction"], "level": e["level"], "entry": e["entry"], "sl": e["sl"], "tp": e["tp"],
                "exit": e["exit"], "outcome": "SKIPPED (contract cap)" if e["skipped"] else e["outcome"],
                "contracts": e["contracts"], "r": e["r"], "pnl": e["pnl"], "bal": bal, "need": START + need})
            if not e["skipped"] and bal <= floor:
                rec.update(outcome="FAIL", day_end=bal, need=START + need); recs.append(rec)
                return "FAIL", recs, (d - start).days + 1
            if bal - START >= need:
                rec.update(outcome="PASS", day_end=bal, need=START + need); recs.append(rec)
                return "PASS", recs, (d - start).days + 1
        best_day = max(best_day, bal - ds)
        eod_high = max(eod_high, bal)
        rec.update(day_end=bal, need=START + max(TARGET, best_day / CONS)); recs.append(rec)
    return "STILL_GOING", recs, (days[-1] - start).days + 1 if days else 0


def main():
    row, df, tr = trades()
    by_day = {}
    for t in tr:
        by_day.setdefault(t["t_in"].date(), []).append(t)
    all_days = sorted(by_day)
    mondays = [pd.Timestamp(m).date() for m in ftmo.get_mondays_full(all_days)]
    # full-history summary for context
    full = []
    for s in mondays:
        o, recs, cd = run_cohort(by_day, [d for d in all_days if d >= s], s)
        full.append((o, cd))
    fp = sorted(cd for o, cd in full if o == "PASS")
    cohorts = []
    for s in mondays[-N_WEEKS:]:
        o, recs, cd = run_cohort(by_day, [d for d in all_days if d >= s], s)
        cohorts.append({"start": str(s), "outcome": o, "calendar_days": cd, "trading_days": len(recs),
                        "end_bal": recs[-1]["day_end"] if recs else START, "days": recs})
    data = {
        "label": f"6E #{ROW} · {row['entry_timeframe']} · {row['tp_mode']} · {row['confirmation_mode']} · rr {row['rr']}",
        "pf": float(row["profit_factor"]), "win": float(row["win_rate_pct"]), "n_trades": int(row["n_trades"]),
        "session": f"{int(row['session_start_minutes'])//60:02d}:{int(row['session_start_minutes'])%60:02d}–"
                   f"{int(row['session_end_minutes'])//60:02d}:{int(row['session_end_minutes'])%60:02d} New York",
        "risk": RISK, "data_range": [str(all_days[0]), str(all_days[-1])],
        "full_n": len(full), "full_pass": round(100 * len(fp) / len(full), 1),
        "full_fail": round(100 * sum(o == "FAIL" for o, _ in full) / len(full), 1),
        "full_med_days": fp[len(fp) // 2] if fp else None,
        "cohorts": cohorts,
        "eff_risk_med": float(np.median([t["contracts"] * t["risk_pts"] * PV for t in tr])),
        "capped_pct": round(100 * np.mean([t["wanted"] > MAX_C for t in tr]), 1),
    }
    tpl = open("cohort_report_5374_template.html", encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, default=float))
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"wrote {OUT}: {len(cohorts)} cohorts, outcomes "
          f"{pd.Series([c['outcome'] for c in cohorts]).value_counts().to_dict()}; full history {data['full_pass']}% pass")


if __name__ == "__main__":
    main()
