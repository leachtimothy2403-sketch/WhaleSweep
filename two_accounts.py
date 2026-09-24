#!/usr/bin/env python3
"""
Two FundedNext Flex accounts (2026-09-24):
  A = 6E #5374 alone
  B = 6E #12654 + MNQ #362
Outputs two_accounts.json with
  - per-cohort (weekly start) outcome + calendar days to pass/fail for A and B
  - B risk grid, A risk sweep ($500 -> $1,500), correlation between A and B
Uses combo_flex.py's event-accurate engine (shared 5-mini/50-micro cap,
realized at exit, account day stop blocks new entries only).
    WS_HISTORY_YEARS=6 py -3 two_accounts.py [grid|sweep|final]
"""
import json
import os
import sys

import numpy as np
import pandas as pd

import combo_flex as C

C.LEGS["6E#12654"] = ("whale_sweep_output_futures/results.csv", 12654, 125_000.0, 10)
LEGS = ["6E#5374", "6E#12654", "MNQ#362"]


def load():
    T = {l: C.leg_trades(l) for l in LEGS}
    first = max(min(t["t_in"] for t in T[l]) for l in LEGS)
    last = min(max(t["t_in"] for t in T[l]) for l in LEGS)
    split = (first + (last - first) * 0.6).date()
    return {l: [t for t in v if first <= t["t_in"] <= last] for l, v in T.items()}, split


def events(T, cfg, stop):
    tr = [t for l in cfg for t in T[l]]
    raw = C.size(tr, cfg)
    return {d: C.walk_day(v, stop) for d, v in raw.items()}


def cohort_rows(ev):
    out = []
    for s, days in C.cohorts(ev):
        oc, i = C.run_eval(ev, days, None)
        out.append({"start": str(s), "outcome": oc, "days": (days[i] - s).days + 1 if days else None})
    return out


def capped_stats(T, leg, risk):
    per = [t["risk_pts"] * t["pv"] for t in T[leg]]
    want = [max(1, round(risk / p)) for p in per]
    cap = 50 // T[leg][0]["units"]
    eff = [min(w, cap) * p for w, p in zip(want, per)]
    return round(float(np.median(eff))), round(100 * np.mean([w > cap for w in want]), 1)


def main(mode):
    T, split = load()
    if mode == "grid":
        rows = []
        for r6 in (375, 500, 750):
            for rm in (250, 375):
                for stop in (None, 1000):
                    r = C.evaluate([t for l in ("6E#12654", "MNQ#362") for t in T[l]],
                                   {"6E#12654": r6, "MNQ#362": rm}, stop, split)
                    rows.append({"6E#12654": r6, "MNQ#362": rm, "stop": stop or "-", **r}); print(rows[-1], flush=True)
        pd.DataFrame(rows).to_csv("two_accounts_B_grid.csv", index=False)
    elif mode == "sweep":
        rows = []
        for risk in (500, 750, 1000, 1250, 1500):
            for stop in (None, 1000):
                r = C.evaluate(T["6E#5374"], {"6E#5374": risk}, stop, split)
                eff, capped = capped_stats(T, "6E#5374", risk)
                rows.append({"risk": risk, "stop": stop or "-", "median actual risk$": eff, "capped%": capped, **r})
                print(rows[-1], flush=True)
        pd.DataFrame(rows).to_csv("two_accounts_A_sweep.csv", index=False)
    else:   # final: cohorts + correlation for chosen configs
        A_cfg, A_stop = {"6E#5374": 500}, None
        B_cfg, B_stop = json.loads(sys.argv[2]), (None if sys.argv[3] == "-" else float(sys.argv[3]))
        evA, evB = events(T, A_cfg, A_stop), events(T, B_cfg, B_stop)
        ca, cb = cohort_rows(evA), cohort_rows(evB)
        dA = pd.Series({d: sum(p for _, p in v) for d, v in evA.items()})
        dB = pd.Series({d: sum(p for _, p in v) for d, v in evB.items()})
        dd = pd.concat([dA, dB], axis=1).fillna(0)
        dd.index = pd.to_datetime(dd.index)
        wk = dd.resample("W").sum()
        m = pd.DataFrame(ca).merge(pd.DataFrame(cb), on="start", suffixes=("_A", "_B"))
        pa, pb = m.outcome_A == "PASS", m.outcome_B == "PASS"
        both = m[pa & pb]
        res = {
            "split": str(split), "A_cfg": A_cfg, "B_cfg": B_cfg, "B_stop": B_stop,
            "corr_daily_pnl": round(float(dd.corr().iloc[0, 1]), 3),
            "corr_weekly_pnl": round(float(wk.corr().iloc[0, 1]), 3),
            "corr_days_to_pass": round(float(both[["days_A", "days_B"]].corr().iloc[0, 1]), 3),
            "p_fail_A": round(100 * (m.outcome_A == "FAIL").mean(), 1), "p_fail_B": round(100 * (m.outcome_B == "FAIL").mean(), 1),
            "p_fail_both": round(100 * ((m.outcome_A == "FAIL") & (m.outcome_B == "FAIL")).mean(), 2),
            "p_at_least_one_pass": round(100 * (pa | pb).mean(), 1),
            "median_first_pass_days": float(np.median(np.fmin(m.days_A.where(pa, np.inf), m.days_B.where(pb, np.inf))[pa | pb])),
            "cohorts": m.to_dict("records"),
        }
        json.dump(res, open("two_accounts.json", "w"), default=str)
        print({k: v for k, v in res.items() if k != "cohorts"})


if __name__ == "__main__":
    main(sys.argv[1])
