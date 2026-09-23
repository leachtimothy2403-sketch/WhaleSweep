#!/usr/bin/env python3
"""
WhaleSweep -- Tradeify Growth $100k evaluation rules + MNQ (Micro
Nasdaq-100 futures) contract-based position sizing.

2026-09-23, per Tim: does the liquidity-sweep strategy work on NASDAQ
futures, evaluated against a real futures prop-firm account instead of
FTMO's CFD-style rules? Tradeify's public lineup (confirmed live,
2026-09-23, via a research pass -- their site currently has NO genuine
2-step evaluation product, only single-phase Growth/Select/Lightning)
has no 2-step program, so this models their single-phase "Growth"
$100k account instead (confirmed with Tim via AskUserQuestion):
  - Profit target: $6,000            -> target_equity = $106,000
  - Max drawdown: $3,500, END-OF-DAY TRAILING (the high-water-mark
    ratchets up only from each day's PRIOR closing balance, never
    intraday) -- this is EXACTLY ftmo_challenge_rules.run_phase()'s
    existing trailing_max_loss=True path (it already updates its
    high-water-mark from day_start_equity, i.e. yesterday's close, not
    today's intraday peak), reused completely unchanged here.
  - Daily loss limit: $2,500 (Tradeify widens this to match the max
    drawdown once profit reaches +6%, i.e. right at the target itself --
    with min_days=1 that window is negligible in practice, so this sim
    conservatively uses the tighter $2,500 for the whole phase).
  - Minimum trading days: 1.
  - Max position size at $100k: 8 NQ / 80 MNQ contracts. This models MNQ.
  - Commission: MNQ ~$1.82/contract round-turn -- see whale_sweep_cost_
    table.py's MNQ entry for how that (plus an assumed slippage
    component) becomes a per-trade points-cost already baked into every
    trade's r_multiple by whale_sweep.py's own backtest_signals().

UNLIKE the FX/CFD side of this codebase (a continuous notional sized as
risk_pct * equity), real futures position sizing is INTEGER CONTRACTS
capped by a hard per-account limit -- a fundamentally different sizing
mechanic, so this module builds its own day-bucketed dollar-P&L series
before handing off to the shared, unmodified run_phase()/
get_mondays_full()/walk_days() engine in ftmo_challenge_rules.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ftmo_challenge_rules as ftmo

START_EQUITY = 100_000.0
TARGET_EQUITY = 106_000.0          # start_equity + $6,000 profit target
MAX_DRAWDOWN = 3_500.0             # EOD-trailing
DAILY_LOSS_LIMIT = 2_500.0
MIN_DAYS = 1
MAX_CONTRACTS_MNQ = 80
POINT_VALUE_MNQ = 2.0


def size_trades_mnq(records: list, df: pd.DataFrame, risk_dollars: float,
                     max_contracts: int = MAX_CONTRACTS_MNQ,
                     point_value: float = POINT_VALUE_MNQ):
    """Converts raw MNQ trade records (from whale_sweep.get_trade_records --
    already cost-adjusted via COST_TABLE["MNQ"]) into a {day: [dollar P&L,
    ...]} dict, sizing each trade to the whole number of contracts closest
    to risking `risk_dollars` given THAT trade's own stop distance in
    points, clipped to [1, max_contracts]. A trade is never skipped for
    being "too small" (always >=1 contract), but IS hard-capped at
    Tradeify's per-account contract limit even when that means risking
    more than `risk_dollars` intended -- the cap is a real account rule,
    not a sizing preference.

    Returns (by_date, all_days_sorted, contracts_used_list).
    """
    by_date: dict = {}
    contracts_used = []
    for r in records:
        risk_points = r["risk"]
        if risk_points <= 0:
            continue
        contracts = int(round(risk_dollars / (risk_points * point_value)))
        contracts = max(1, min(max_contracts, contracts))
        contracts_used.append(contracts)
        dollar_pnl = contracts * r["r_multiple"] * risk_points * point_value
        day = df.index[r["entry_idx"]].date()
        by_date.setdefault(day, []).append(dollar_pnl)
    all_days = sorted(by_date.keys())
    return by_date, all_days, contracts_used


def simulate_growth(records: list, df: pd.DataFrame, risk_dollars: float) -> dict:
    """Weekly-Monday-cohort walk of Tradeify Growth $100k's rules against
    one MNQ candidate's real trade records -- mirrors portfolio_optimizer.
    py's own cohort-loop convention, reusing run_phase()/get_mondays_full()/
    walk_days() completely unchanged (single-phase, so no Phase 2 handoff
    is needed, unlike the FTMO 2-step evaluation elsewhere in this repo)."""
    by_date, all_days, contracts_used = size_trades_mnq(records, df, risk_dollars)
    if len(all_days) < 8:
        return {"n_cohorts": 0}

    mondays = ftmo.get_mondays_full(all_days)
    outcomes = []
    for start in mondays:
        days = ftmo.walk_days(by_date, all_days, start)
        outcome, reason, days_used, n_trades, end_equity, last_idx = ftmo.run_phase(
            by_date, days, risk_amt=1.0, target_equity=TARGET_EQUITY,
            max_loss_buffer=MAX_DRAWDOWN, trailing_max_loss=True,
            daily_loss_limit=DAILY_LOSS_LIMIT, min_days=MIN_DAYS,
            start_equity=START_EQUITY)
        calendar_days = (days[last_idx] - start).days + 1 if 0 <= last_idx < len(days) else None
        outcomes.append({"outcome": outcome, "reason": reason,
                          "calendar_days": calendar_days, "end_equity": end_equity})

    n = len(outcomes)
    n_pass = sum(1 for o in outcomes if o["outcome"] == "PASS")
    n_fail = sum(1 for o in outcomes if o["outcome"] == "FAIL")
    pass_days = sorted(o["calendar_days"] for o in outcomes if o["outcome"] == "PASS" and o["calendar_days"])
    med_days = pass_days[len(pass_days) // 2] if pass_days else None
    return {
        "n_cohorts": n, "n_pass": n_pass, "n_fail": n_fail,
        "n_still_going": n - n_pass - n_fail,
        "pass_pct": round(100.0 * n_pass / n, 1) if n else None,
        "fail_pct": round(100.0 * n_fail / n, 1) if n else None,
        "median_calendar_days_to_pass": med_days,
        "avg_contracts": round(float(np.mean(contracts_used)), 2) if contracts_used else None,
        "max_contracts_hit_pct": round(100.0 * sum(1 for c in contracts_used if c >= MAX_CONTRACTS_MNQ) / len(contracts_used), 1) if contracts_used else None,
        "n_trades_total": len(contracts_used),
    }
