#!/usr/bin/env python3
"""
WhaleSweep -- one-off: sweep risk_multiplier_k for one fixed candidate
combo and show the trades/day vs. pass_pct vs. margin-rejection tradeoff
under the 80% margin cap, in one table.

Answers Tim's 2026-09-23 question: does lowering risk-per-trade (k) let
more of the signaled trades through the margin gate, and what does that
cost in pass_pct?

Run from the WhaleSweep repo root, with the real 6-year precomputed
parquet files present (this needs the VPS's data, not the laptop's --
see chat history 2026-09-23 for why):

    py -3 sweep_k_tradeoff.py
"""
import functools
import portfolio_optimizer as po

# The size=4 combo from portfolio_optimizer.csv (2026-09-23 6yr run):
# GER40/3min ss=180 (local_rank=9) | NDX100/3min ss=180 (local_rank=0) |
# EURUSD/3min ss=180 (local_rank=5) | NDX100/3min ss=420 (local_rank=6)
TARGETS = [
    ("GER40", "3min", 180, 9),
    ("NDX100", "3min", 180, 0),
    ("EURUSD", "3min", 180, 5),
    ("NDX100", "3min", 420, 6),
]

pool_raw = po.load_candidate_pool("whale_sweep_output/candidate_screen.csv", "whale_sweep_output/top_strategies.json")

df_cache = {}
combo = []
for asset, tf, ss, rank in TARGETS:
    row = next(r for r in pool_raw if r["asset"] == asset and r["entry_timeframe"] == tf
               and r.get("session_start_minutes") == ss and r["local_rank"] == rank)
    cd = po.build_candidate_data(row, df_cache)
    combo.append(cd)
    print(f"{cd['label']}: {cd['n_trades']} raw trades, kelly_f={cd['kelly_f']:.4f}")

sim_fn = functools.partial(po.simulate_portfolio_margin_gated, capacity_frac=0.8)

# k=0.0677 is the value portfolio_optimizer.py found landing near the 50%
# target for this combo -- sweep from well below it up to it, plus a bit
# above for context.
K_GRID = [0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05, 0.055, 0.06, 0.0677, 0.075, 0.085]

print(f"\n{'k':>8} {'pass%':>7} {'median_days':>12} {'signaled/day':>13} {'accepted/day':>13} {'%rejected':>10}  per_asset_risk_pct")
for k in K_GRID:
    res = sim_fn(combo, k, "2step")
    n_cohorts = res["n_cohorts"]
    span_days = n_cohorts * 7  # weekly cohort anchors -> ~exact window length
    n_total = res["n_trades_total"]
    n_rej = res["n_trades_rejected"]
    n_acc = n_total - n_rej
    per_asset = {c["asset"]: round(float(100 * k * c["kelly_f"]), 3) for c in combo}
    print(f"{k:8.4f} {res['pass_pct']:7.1f} {str(res['median_days_to_pass']):>12} "
          f"{n_total/span_days:13.2f} {n_acc/span_days:13.2f} {100*n_rej/n_total:10.1f}  {per_asset}")
