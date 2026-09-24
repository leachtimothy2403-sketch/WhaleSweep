#!/usr/bin/env python3
"""
Two strategies in ONE FundedNext Flex $100k account (2026-09-24):
MNQ #362 (whale_sweep_output_mnq_6yr/results.csv) + a 6E candidate
(whale_sweep_output_futures/results.csv), evaluation AND funded stage.

Event-accurate within a day (unlike the single-strategy sims):
  - trades from both legs merged and processed in real entry-time order;
    a trade's P&L is realized at its real exit time;
  - MLL / pass / payout checks happen on realized balance;
  - account-level day loss stop: no NEW entries once realized day P&L <= -stop;
  - shared contract limit 50 micro-units (1 MNQ = 1 unit, 1 6E mini = 10
    units = "5 minis or 50 micros"); a new trade is shrunk to the free
    units, skipped if none;
  - one-position-per-leg is NOT enforced (the backtest's own overlapping
    trades are kept, as in the search).
Evaluation: $5k target, $2.5k EOD-trailing MLL (lock $100,100), 40%
consistency (target -> best_day/0.4). Funded: same MLL until first payout
then locked at $100,100; payout after 5 benchmark days (day >= $200) and
profit >= max($500, W); withdraw min(50% profit, $2,500), 95% split;
5-payout cap (help centre) and 12-month uncapped both reported.

    WS_HISTORY_YEARS=6 py -3 combo_flex.py
"""
import itertools
import sys

import numpy as np
import pandas as pd

import whale_sweep as ws
import ftmo_challenge_rules as ftmo
from screen_flex import params_from_row

START, MLL, LOCK = 100_000.0, 2_500.0, 100_100.0
UNITS_CAP = 50
LEGS = {
    "MNQ#362": ("whale_sweep_output_mnq_6yr/results.csv", 362, 2.0, 1),        # $/pt, units per contract
    "6E#38431": ("whale_sweep_output_futures/results.csv", 38431, 125_000.0, 10),
    "6E#5374": ("whale_sweep_output_futures/results.csv", 5374, 125_000.0, 10),
}


def leg_trades(name):
    path, row_i, pv, units = LEGS[name]
    row = pd.read_csv(path).iloc[row_i]
    df = ws.load_precomputed(row["asset"], row["entry_timeframe"])
    rec = ws.get_trade_records(df, params_from_row(row))
    idx = df.index
    return [{"leg": name, "t_in": idx[r["entry_idx"]], "t_out": idx[r["exit_idx"]],
             "risk_pts": r["risk"], "r": r["r_multiple"], "pv": pv, "units": units} for r in rec]


def size(trades, risk_by_leg):
    out = []
    for t in trades:
        risk = risk_by_leg[t["leg"]]
        if not risk:
            continue
        per = t["risk_pts"] * t["pv"]
        n = max(1, int(round(risk / per)))
        out.append({**t, "n": n})
    by_day = {}
    for t in sorted(out, key=lambda x: x["t_in"]):
        by_day.setdefault(t["t_in"].date(), []).append(t)
    return by_day


def walk_day(trades, day_stop):
    """Yields realized P&L events for one day, in time order, applying the
    day stop and the shared unit cap. Returns list of (time, pnl)."""
    open_, events, realized = [], [], 0.0
    for t in trades:
        # realize everything that closed before this entry
        still = []
        for o in sorted(open_, key=lambda x: x["t_out"]):
            if o["t_out"] <= t["t_in"]:
                events.append((o["t_out"], o["pnl"])); realized += o["pnl"]
            else:
                still.append(o)
        open_ = still
        if day_stop is not None and realized <= -day_stop:
            continue
        free = UNITS_CAP - sum(o["n"] * o["units"] for o in open_)
        n = min(t["n"], free // t["units"])
        if n <= 0:
            continue
        pnl = n * t["r"] * t["risk_pts"] * t["pv"]
        open_.append({**t, "n": n, "pnl": pnl})
    for o in sorted(open_, key=lambda x: x["t_out"]):
        events.append((o["t_out"], o["pnl"]))
    return events


def run_eval(by_day, days, day_stop):
    bal, eod_high, best_day = START, START, 0.0
    for i, d in enumerate(days):
        floor = min(eod_high - MLL, LOCK)
        ds = bal
        for _, pnl in by_day[d]:
            bal += pnl
            if bal <= floor:
                return "FAIL", i
            need = max(5_000.0, max(best_day, bal - ds) / 0.4)
            if bal - START >= need:
                return "PASS", i
        best_day = max(best_day, bal - ds)
        eod_high = max(eod_high, bal)
    return "STILL", len(days) - 1


def run_funded(by_day, days, start, day_stop, W=5_000.0, max_payouts=5, horizon=None):
    bal, eod_high, locked, bench, paid = START, START, False, 0, []
    for d in days:
        if horizon and (d - start).days > horizon:
            return paid, "horizon"
        floor = LOCK if locked else min(eod_high - MLL, LOCK)
        ds = bal
        for _, pnl in by_day[d]:
            bal += pnl
            if bal <= floor:
                return paid, "breach"
        if bal - ds >= 200:
            bench += 1
        eod_high = max(eod_high, bal)
        p = bal - START
        if bench >= 5 and p >= max(500.0, W):
            amt = min(0.5 * p, 2_500.0)
            bal -= amt; paid.append(((d - start).days + 1, 0.95 * amt)); bench, locked = 0, True
            if max_payouts and len(paid) >= max_payouts:
                return paid, "done"
    return paid, "end"


def cohorts(by_day, start_from=None, start_before=None, need_days=0):
    all_days = sorted(by_day)
    for m in ftmo.get_mondays_full(all_days):
        s = pd.Timestamp(m).date()
        if (start_from and s < start_from) or (start_before and s >= start_before):
            continue
        if need_days and (all_days[-1] - s).days < need_days:
            continue
        days = [d for d in all_days if d >= s]
        if days:
            yield s, days


def evaluate(trades, risk_by_leg, day_stop, split):
    raw = size(trades, risk_by_leg)
    by_day = {d: walk_day(v, day_stop) for d, v in raw.items()}   # realized events, precomputed once
    res = {}
    for tag, kw in (("OOS", {"start_from": split}), ("full", {})):
        o = [(run_eval(by_day, days, day_stop), s, days) for s, days in cohorts(by_day, **kw)]
        pd_ = sorted((days[i] - s).days + 1 for (oc, i), s, days in o if oc == "PASS")
        res[f"{tag} pass%"] = round(100 * len(pd_) / len(o), 1)
        res[f"{tag} days"] = pd_[len(pd_) // 2] if pd_ else None
        if tag == "OOS":
            res["OOS p90"] = pd_[int(0.9 * len(pd_))] if pd_ else None
    f5 = [run_funded(by_day, days, s, day_stop) for s, days in cohorts(by_day, need_days=365)]
    tot = [sum(p for _, p in paid) for paid, _ in f5]
    fifth = sorted(paid[4][0] for paid, _ in f5 if len(paid) == 5)
    f12 = [run_funded(by_day, days, s, day_stop, max_payouts=None, horizon=365) for s, days in cohorts(by_day, need_days=365)]
    res.update({
        "funded E$ (5 cap)": round(np.mean(tot)), "reach 5 payouts%": round(100 * len(fifth) / len(f5), 1),
        "days to 5th": fifth[len(fifth) // 2] if fifth else None,
        "funded breach%": round(100 * np.mean([w == "breach" for _, w in f5]), 1),
        "12m E$ uncapped": round(np.mean([sum(p for _, p in paid) for paid, _ in f12])),
    })
    return res


def main():
    six_e = sys.argv[1] if len(sys.argv) > 1 else "6E#38431"
    tr = leg_trades("MNQ#362") + leg_trades(six_e)
    first = max(min(t["t_in"] for t in tr if t["leg"] == l) for l in ("MNQ#362", six_e))
    last = min(max(t["t_in"] for t in tr if t["leg"] == l) for l in ("MNQ#362", six_e))
    tr = [t for t in tr if first <= t["t_in"] <= last]
    split = (first + (last - first) * 0.6).date()
    print(f"legs MNQ#362 + {six_e}: {first.date()} .. {last.date()}, OOS from {split}")

    # daily P&L correlation at $500 each
    d = pd.DataFrame([{"day": t["t_in"].date(), "leg": t["leg"], "R": t["r"]} for t in tr])
    daily = d.pivot_table(index="day", columns="leg", values="R", aggfunc="sum").fillna(0)
    print(f"daily R correlation: {daily.corr().iloc[0, 1]:.3f}; days with trades: "
          f"MNQ {int((daily['MNQ#362'] != 0).sum())}, 6E {int((daily[six_e] != 0).sum())}, both {int(((daily != 0).all(axis=1)).sum())}")

    import os
    out_csv = f"combo_flex_{six_e.replace('#', '_')}.csv"
    done = set()
    if os.path.exists(out_csv):
        prev = pd.read_csv(out_csv)
        done = {(a, b, str(c)) for a, b, c in zip(prev["MNQ risk"], prev["6E risk"], prev["day_stop"])}
    rows = []
    grid = [(m, e) for m, e in itertools.product((0, 250, 375, 500, 750), (0, 250, 375, 500, 750)) if m or e]
    for (rm, re_), stop in itertools.product(grid, (None, 500, 750, 1000)):
        if stop is not None and stop < max(rm, re_) * 0.99 and stop != 500:
            continue
        if (rm, re_, str(stop or "-")) in done:
            continue
        r = evaluate(tr, {"MNQ#362": rm, six_e: re_}, stop, split)
        row = {"MNQ risk": rm, "6E risk": re_, "day_stop": stop or "-", **r}
        pd.DataFrame([row]).to_csv(out_csv, mode="a", header=not os.path.exists(out_csv), index=False)
        print(row, flush=True)
    t = pd.read_csv(out_csv)
    pd.set_option("display.width", 260)
    print(t.sort_values("funded E$ (5 cap)", ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    main()
