# WhaleSweep — Strategy & Portfolio Overview

*Compiled 2026-09-20. Figures below are historical backtest statistics from real trade records, not live trading results.*

## Strategy

WhaleSweep is an institutional-liquidity-sweep reversal strategy: it looks for price sweeping a resting liquidity level (a prior swing high/low) and then reversing, and trades the reversal. Each (asset, timeframe, session) combination found by the parameter search is a separate "candidate"; the live portfolio combines several decorrelated candidates rather than trading one strategy alone, targeting FTMO's 2-step challenge (5% then 10% profit targets, static equity floor, $5,000 daily loss limit).

**Important status caveat before reading any numbers below**: every figure here is calibrated on a 2-year historical window (2024-08 to 2026-08) and does **not** yet include: (1) the just-relaunched 6-year search, which found that this strategy's edge has visibly declined since 2016 (a 63.5% pass rate in 2016-2018 eroding to a 40.4% trough in 2022-2024) — the current candidate pool has not been re-selected against that longer, harder history; (2) any fix for the leverage/margin gap described below, which the simulation currently ignores entirely. Treat this as a snapshot of "what the pipeline currently outputs," not a final, tradeable specification.

## Portfolio composition

Two candidate combos are live at the moment, both drawn from the current (2-year-window, cost-corrected) candidate pool. Neither has been re-validated against the 6-year history or a margin fix yet.

### "New" combo — current top pick post cost-fix (item 14, 2026-09-20)

| Asset | Timeframe | Session start | Risk % of equity | Local data? |
|---|---|---|---|---|
| GBPUSD | 3min | 03:00 NY | 1.361% | VPS only |
| GER40 | 5min | 03:00 NY | 2.122% | ✅ local |
| EURUSD | 3min | 03:00 NY | 0.613% | ✅ local |
| SPX500 | 5min | 03:00 NY | 0.392% | VPS only |
| XAUUSD | 3min | 03:00 NY | 1.365% | ✅ local |
| NDX100 | 5min | 03:00 NY | 2.027% | ✅ local |

Portfolio-level (from `portfolio_optimizer.py`, all 6 legs, 2-year window): avg pairwise correlation 0.057, risk multiplier k=0.0387, historical pass rate 50.2%, median days-to-pass 10.

### "Old" combo — pre-cost-fix baseline, still hardcoded as the script default

| Asset | Timeframe | Session start | Risk % of equity | Local data? |
|---|---|---|---|---|
| GER40 | 3min | 03:00 NY | 1.106% | ✅ local |
| EURUSD | 5min | 03:00 NY | 0.962% | ✅ local |
| GBPUSD | 5min | 03:00 NY | 0.952% | VPS only |
| XAUUSD | 5min | 03:00 NY | 1.518% | ✅ local |
| US30 | 3min | 03:00 NY | 0.717% | VPS only |
| SPX500 | 5min | 03:00 NY | 1.105% | VPS only |

Portfolio-level (2-year window, pre-real-cost): avg pairwise correlation 0.111, k=0.0209, historical pass rate 52.3%, median days-to-pass 10.

Both combos concentrate every leg at the same 03:00 NY session — see the leverage/margin section of the full documentation for why that's a real concurrency risk, not just a coincidence of the search.

## Per-asset performance (individual legs, real trade records, 2-year local window)

Only assets with precomputed data on the laptop could be measured directly; GBPUSD, SPX500, and US30 need the VPS and are not included below.

| Leg | Trades | Win rate | Avg R/trade | Median R/trade | Avg winner (R) | Avg loser (R) | Trades/week | Data span |
|---|---|---|---|---|---|---|---|---|
| GER40 / 5min (new combo) | 221 | 51.1% | 0.66 | 0.14 | 2.20 | -0.95 | 2.1 | 2024-08 to 2026-08 |
| EURUSD / 3min (new combo) | 801 | 23.6% | 1.18 | -1.29 | 9.55 | -1.40 | 7.7 | 2024-08 to 2026-08 |
| XAUUSD / 3min (new combo) | 581 | 47.2% | 0.77 | -0.04 | 2.40 | -0.69 | 5.6 | 2024-08 to 2026-08 |
| NDX100 / 5min (new combo) | 1751 | 55.6% | 0.55 | 0.15 | 1.66 | -0.84 | 16.8 | 2024-08 to 2026-08 |
| GER40 / 3min (old combo) | 382 | 47.1% | 1.68 | -0.47 | 4.81 | -1.12 | 3.7 | 2024-08 to 2026-08 |
| EURUSD / 5min (old combo) | 821 | 62.4% | 3.64 | 1.45 | 6.65 | -1.35 | 7.9 | 2024-08 to 2026-08 |
| XAUUSD / 5min (old combo) | 397 | 56.4% | 1.09 | 0.21 | 2.44 | -0.66 | 3.8 | 2024-08 to 2026-08 |

**Read the "avg R" column carefully — this is a fat-tailed, asymmetric strategy, not a steady-edge one.** Several legs have win rates well under 50% (EURUSD/3min is only 23.6%) but a strongly positive average R, because winners run far larger than losers when the reversal works (up to 53R on a single EURUSD/3min trade, 59R on EURUSD/5min). The median R is frequently negative or near zero even when the average is strongly positive — most individual trades are small losers or scratch trades, and the edge comes from a minority of outsized winners. This is exactly why the strategy is combined across several candidates (Kelly-weighted) rather than run as a single bet, and why the historical pass-rate simulation (which looks at actual sequences of trades, not just averages) is the meaningful metric rather than any single per-trade statistic.

Local subset trades/week, summed (not the full portfolio, since 2-3 legs of each combo are VPS-only): new-combo local 4 legs ≈ 32/week (~4.6/day), old-combo local 3 legs ≈ 15/week (~2.2/day). The full 6-leg book was separately measured (on the VPS, full ~10-year history) at roughly 10 trades/day — consistent with the missing legs contributing meaningfully once included.

## Margin-gating: a promising, not-yet-adopted fix

The current simulation has no concept of margin or leverage at all. A direct measurement found real positions do overlap in clock time (32-35% of new trade opens happen while another leg is already open) and combined margin exceeds the account's full equity roughly 1-2% of trading time on just the locally-checkable subset of legs — a real, not theoretical, problem given every leg shares the same session.

A follow-up check — refusing to open any trade that would exceed a fixed margin capacity — found this *raises* historical pass rate rather than costing it, because it suppresses the dominant historical failure mode (several positions losing on the same day, breaching the daily-loss limit):

| Combo (local subset) | Margin cap | Pass rate | Median days-to-pass |
|---|---|---|---|
| New (4 of 6 legs) | none (baseline) | 26.7% | 10 |
| New (4 of 6 legs) | 100% of equity | 39.0% | 10 |
| New (4 of 6 legs) | 80% of equity | 42.9% | 10 |
| New (4 of 6 legs) | 50% of equity | 48.6% | 11 |
| Old (3 of 6 legs) | none (baseline) | 85.7% | 11 |
| Old (3 of 6 legs) | 100% of equity | 88.6% | 11 |
| Old (3 of 6 legs) | 80% of equity | 88.6% | 11 |
| Old (3 of 6 legs) | 50% of equity | 93.3% | 11 |

The subset baseline pass rates (26.7%/85.7%) aren't comparable to the calibrated ~50% full-portfolio target — only the shift (baseline → margin-gated) at each threshold is the clean signal. A loose-to-moderate cap gets most of this lift with speed essentially untouched; only the tightest cap tested trades meaningful speed for extra pass-rate. This has not yet been implemented in the real sizing pipeline or extended to the full 6-leg portfolio — see the full documentation for the open decision.

## What would make this document final

1. Re-run candidate discovery and screening against the 6-year history (already relaunched, not yet merged) so the pool reflects the 2022-2024 weak stretch, not just the favorable 2024-2026 window it's currently calibrated on.
2. A decision on how to fix the leverage/margin gap, followed by extending the margin-gate check to the full 6-leg portfolio (needs GBPUSD, SPX500, US30 — VPS only) and recalibrating the risk multiplier under that constraint rather than just checking its effect on today's fixed sizing.
3. A `portfolio_report.py` deep-dive (full days-to-pass distribution, pass-rate-over-time buckets) on whatever combo the above two converge on.
