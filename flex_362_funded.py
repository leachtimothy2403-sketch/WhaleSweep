#!/usr/bin/env python3
"""
Candidate #362 (MNQ 5min fixed_rr, whale_sweep_output_mnq_6yr/results.csv row 362) on FundedNext Flex $100k:
  part A -- evaluation at higher risk + contract-limit check
  part B -- FUNDED account: expected total payout per funded account vs risk,
            daily loss stop and payout-request threshold.

Funded rules modeled (FundedNext help centre, 2026-09-24):
  - MLL $2,500 EOD trailing (same as evaluation) until the first payout;
    from the first payout on the floor is LOCKED at $100,100.
  - No daily loss limit, no consistency rule.
  - Payout eligibility per cycle: 5 benchmark days (net day P&L >= $200)
    since the last payout AND profit (balance - $100,000) >= $500.
    Assumed: benchmark-day count resets after each payout; request at EOD.
  - Withdrawal: up to 50% of profit, max $2,500 per request; trader gets 95%.
  - Account life: help centre says "concluded after 5 withdrawals"; per Tim it
    goes to review for a live transfer and otherwise keeps paying. Both are
    reported: capped at 5 payouts, and uncapped within a 12-month horizon.
Policy knob W: only request a payout once profit >= W (keeps a cushion
above the $100,100 lock after the withdrawal).
    WS_HISTORY_YEARS=6 py -3 flex_362_funded.py
"""
import itertools

import numpy as np
import pandas as pd

import whale_sweep as ws
import ftmo_challenge_rules as ftmo
import tradeify_challenge_rules as tr
import fundednext_flex_rules as fx
from screen_flex import params_from_row

ROW = 362
START, MLL, LOCK = 100_000.0, 2_500.0, 100_100.0
BENCH_DAY, MIN_PROFIT, MAX_WD, SPLIT = 200.0, 500.0, 2_500.0, 0.95


def run_funded(by_date, days, start, day_loss_stop=None, W=500.0, max_payouts=5, horizon_days=None):
    bal, eod_high, locked, bench, paid = START, START, False, 0, []
    for day in days:
        if horizon_days is not None and (day - start).days > horizon_days:
            return paid, "horizon"
        floor = LOCK if locked else min(eod_high - MLL, LOCK)
        ds = bal
        for r in by_date[day]:
            if day_loss_stop is not None and bal - ds <= -day_loss_stop:
                break
            bal += r
            if bal <= floor:
                return paid, "breach"
        if bal - ds >= BENCH_DAY:
            bench += 1
        eod_high = max(eod_high, bal)
        profit = bal - START
        if bench >= 5 and profit >= max(MIN_PROFIT, W):
            amt = min(0.5 * profit, MAX_WD)
            bal -= amt
            paid.append(SPLIT * amt)
            bench, locked = 0, True
            if max_payouts and len(paid) >= max_payouts:
                return paid, "max_payouts"
    return paid, "data_end"


def funded_stats(rec, df, risk, stop, W, max_payouts, horizon_days, start_from=None, start_before=None):
    by_date, all_days, _ = tr.size_trades_mnq(rec, df, risk, max_contracts=50)
    res = []
    last = all_days[-1]
    for m in ftmo.get_mondays_full(all_days):
        s = pd.Timestamp(m).date()
        if start_from and s < start_from or start_before and s >= start_before:
            continue
        if horizon_days and (last - s).days < horizon_days:
            continue          # need a full horizon of data after the start
        days = ftmo.walk_days(by_date, all_days, s)
        if not days:
            continue
        paid, why = run_funded(by_date, days, s, stop, W, max_payouts, horizon_days)
        res.append((sum(paid), len(paid), why))
    tot = np.array([r[0] for r in res]); n = np.array([r[1] for r in res])
    return {"cohorts": len(res), "E_payout$": round(tot.mean()), "median$": round(float(np.median(tot))),
            "p_any_payout%": round(100 * (n > 0).mean(), 1), "avg_n_payouts": round(n.mean(), 2),
            "breach%": round(100 * np.mean([r[2] == "breach" for r in res]), 1)}


def main():
    pool = pd.read_csv("whale_sweep_output_mnq_6yr/results.csv")
    row = pool.iloc[ROW]
    df = ws.load_precomputed("MNQ", row["entry_timeframe"])
    rec = ws.get_trade_records(df, params_from_row(row))
    split = (df.index[0] + (df.index[-1] - df.index[0]) * 0.6).date()
    pd.set_option("display.width", 250)

    # ---- A: contracts needed ----
    stops = np.array([r["risk"] for r in rec])
    print(f"#{ROW}: {len(rec)} trades; stop distance (NQ points) median {np.median(stops):.1f}, "
          f"p10 {np.percentile(stops,10):.1f}, p90 {np.percentile(stops,90):.1f}, min {stops.min():.1f}")
    rows = []
    for risk in (500, 750, 1000, 1250, 1500):
        c = np.clip(np.round(risk / (stops * 2.0)), 1, None)
        eff = np.minimum(c, 50) * stops * 2.0
        rows.append({"risk$": risk, "MNQ med": int(np.median(c)), "MNQ p90": int(np.percentile(c, 90)),
                     "MNQ max": int(c.max()), "trades >50 MNQ %": round(100 * (c > 50).mean(), 1),
                     "median actual risk$ (capped)": round(float(np.median(eff))),
                     "p10 actual risk$": round(float(np.percentile(eff, 10)))})
    print(pd.DataFrame(rows).to_string(index=False))

    # ---- A: evaluation ----
    rows = []
    for risk in (500, 750, 1000, 1250, 1500):
        for stop in (None, 500, 750, 1000, 1500):
            if stop is not None and stop > risk:
                continue
            o = fx.simulate_flex(rec, df, risk, start_from=split, day_loss_stop=stop)
            f = fx.simulate_flex(rec, df, risk, day_loss_stop=stop)
            rows.append({"risk$": risk, "day_stop$": stop or "-", "OOS pass%": o["pass%"], "OOS days": o["days_med"],
                         "OOS p90": o["days_p90"], "full pass%": f["pass%"], "full days": f["days_med"],
                         "capped%": f["capped%"]})
    print("\nEVALUATION (day_stop = stop for the day once day P&L <= -X; X = risk means 'after one loss')")
    print(pd.DataFrame(rows).to_string(index=False))

    # ---- B: funded ----
    out = []
    for risk, stopk, W in itertools.product((250, 500, 750, 1000, 1250, 1500), ("none", "1 loss"),
                                             (500, 1000, 2000, 3000, 5000)):
        stop = None if stopk == "none" else risk
        a = funded_stats(rec, df, risk, stop, W, max_payouts=5, horizon_days=None)
        b = funded_stats(rec, df, risk, stop, W, max_payouts=None, horizon_days=365)
        c = funded_stats(rec, df, risk, stop, W, max_payouts=5, horizon_days=None, start_from=split)
        out.append({"risk$": risk, "day_stop": stopk, "request_at_profit>=": W,
                    "5cap E$": a["E_payout$"], "5cap P(any)%": a["p_any_payout%"], "5cap avg#": a["avg_n_payouts"],
                    "5cap breach%": a["breach%"], "5cap OOS E$": c["E_payout$"],
                    "12m uncapped E$": b["E_payout$"], "12m avg#": b["avg_n_payouts"], "12m breach%": b["breach%"]})
    t = pd.DataFrame(out)
    t.to_csv("flex_362_funded.csv", index=False)
    print("\nFUNDED -- best 15 by expected payout (5-payout cap, full history):")
    print(t.sort_values("5cap E$", ascending=False).head(15).to_string(index=False))
    print("\nFUNDED -- best per risk level:")
    print(t.loc[t.groupby("risk$")["5cap E$"].idxmax()].to_string(index=False))
    print("\nFUNDED -- best 10 by 12-month uncapped expected payout:")
    print(t.sort_values("12m uncapped E$", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
