"""
WhaleSweep -- FundedNext Futures "Flex" $100k evaluation rules (2026-09-24).

Rules modeled (FundedNext site / help centre, checked 2026-09-24, plus Tim):
  - Profit target: $5,000.
  - Max loss limit (MLL): $2,500 END-OF-DAY trailing -- the floor is
    (highest END-OF-DAY balance so far) - $2,500, fixed for the whole next
    day, never moves down, and stops trailing ("locks") at LOCK_LEVEL.
    Breach = equity touches the floor at any time (checked trade by trade).
    Per Tim the lock level is $100,100 (FundedNext's own page says "locks
    once it reaches the initial balance"; parameter below).
  - NO daily loss limit.
  - Consistency (evaluation only): best single day's profit must be
    <= 40% of total profit. If it isn't, the target is raised to
    best_day / 0.40 (not a breach).
  - Minimum trading days: MIN_DAYS (unclear from public sources -- run
    with 1 and with a stricter value to see the sensitivity).
  - Max position: 5 minis / 50 micros -> 50 MNQ.
  - All positions flat by 15:10 CT (16:10 ET) -- the WhaleSweep session
    windows already end earlier, so no effect on the backtest.
Pass is checked after every trade, i.e. it models a bot that stops
trading the instant the (possibly raised) target and min days are met.
"""
from __future__ import annotations

import numpy as np

import ftmo_challenge_rules as ftmo
import tradeify_challenge_rules as tr

START_EQUITY = 100_000.0
PROFIT_TARGET = 5_000.0
MLL = 2_500.0
LOCK_LEVEL = 100_100.0
CONSISTENCY = 0.40
MAX_CONTRACTS_MNQ = 50


def run_flex(by_date, days, min_days=1, lock_level=LOCK_LEVEL, consistency=CONSISTENCY,
             profit_target=PROFIT_TARGET, mll=MLL):
    equity, eod_high, best_day = START_EQUITY, START_EQUITY, 0.0
    target_raised = False
    for i, day in enumerate(days):
        floor = min(eod_high - mll, lock_level)
        day_start = equity
        for r in by_date[day]:
            equity += r
            if equity <= floor:
                return "FAIL", "max_loss", i, target_raised
            today = equity - day_start
            best = max(best_day, today)
            profit = equity - START_EQUITY
            need = profit_target
            if consistency and best > consistency * need:
                need = max(need, best / consistency)
            if profit >= need and (i + 1) >= min_days:
                return "PASS", None, i, target_raised or need > profit_target
        best_day = max(best_day, equity - day_start)
        if consistency and best_day > consistency * profit_target:
            target_raised = True
        eod_high = max(eod_high, equity)
    return "STILL_GOING", None, len(days) - 1, target_raised


def simulate_flex(records, df, risk_dollars, min_days=1, **kw):
    by_date, all_days, contracts = tr.size_trades_mnq(records, df, risk_dollars,
                                                      max_contracts=MAX_CONTRACTS_MNQ)
    out = []
    for start in ftmo.get_mondays_full(all_days):
        days = ftmo.walk_days(by_date, all_days, start)
        if not days:
            continue
        o, reason, last, raised = run_flex(by_date, days, min_days=min_days, **kw)
        out.append({"outcome": o, "reason": reason, "raised": raised,
                    "cal_days": (days[last] - start).days + 1})
    return _summ(out, contracts, MAX_CONTRACTS_MNQ)


def simulate_tradeify(records, df, risk_dollars):
    """Tradeify Growth $100k (same engine as tradeify_challenge_rules.simulate_growth),
    returned in the same shape as simulate_flex for side-by-side tables."""
    by_date, all_days, contracts = tr.size_trades_mnq(records, df, risk_dollars)
    out = []
    for start in ftmo.get_mondays_full(all_days):
        days = ftmo.walk_days(by_date, all_days, start)
        if not days:
            continue
        o, reason, _, _, _, last = ftmo.run_phase(
            by_date, days, risk_amt=1.0, target_equity=tr.TARGET_EQUITY,
            max_loss_buffer=tr.MAX_DRAWDOWN, trailing_max_loss=True,
            daily_loss_limit=tr.DAILY_LOSS_LIMIT, min_days=tr.MIN_DAYS, start_equity=tr.START_EQUITY)
        out.append({"outcome": o, "reason": reason, "raised": False,
                    "cal_days": (days[last] - start).days + 1})
    return _summ(out, contracts, tr.MAX_CONTRACTS_MNQ)


def _summ(out, contracts, cap):
    n = len(out)
    p = [o for o in out if o["outcome"] == "PASS"]
    f = [o for o in out if o["outcome"] == "FAIL"]
    d = sorted(o["cal_days"] for o in p)
    q = lambda x: d[min(len(d) - 1, int(x * len(d)))] if d else None
    reasons = {}
    for o in f:
        reasons[o["reason"]] = reasons.get(o["reason"], 0) + 1
    return {
        "cohorts": n, "pass%": round(100 * len(p) / n, 1), "fail%": round(100 * len(f) / n, 1),
        "still%": round(100 * (n - len(p) - len(f)) / n, 1),
        "days_med": q(0.5), "days_p75": q(0.75), "days_p90": q(0.9),
        "fail_reasons": reasons,
        "target_raised%": round(100 * sum(o["raised"] for o in out) / n, 1),
        "avg_contracts": round(float(np.mean(contracts)), 1),
        "capped%": round(100 * np.mean([c >= cap for c in contracts]), 1),
    }
