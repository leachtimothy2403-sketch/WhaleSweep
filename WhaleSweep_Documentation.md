# WhaleSweep — Full Technical Documentation

*Compiled 2026-09-20. This is a reference document, not a running session log — for the turn-by-turn history of who-found-what-when, see `claude/handoff.md` in the WhaleSweep Claude Project.*

## 1. What WhaleSweep is

WhaleSweep is an institutional-liquidity-sweep reversal trading strategy being tuned for an FTMO prop-firm challenge, currently targeting FTMO's **2-step, 5%+10%, static-floor** challenge type (Phase 1: reach $110,000 from $100,000 starting equity without breaching a $90,000 static floor or a $5,000 daily loss limit, minimum 4 trading days; Phase 2: same floors, target $105,000 from a reset $100,000, minimum 4 more trading days).

The design goal is not a single strategy but a **portfolio** of several decorrelated (asset, session) candidates, combined with Kelly-derived relative weights and then scaled by a single risk multiplier so the portfolio's overall historical challenge pass rate lands near **50%**, while passing **as fast as possible** once it does pass.

## 2. Environments

Two separate machines are involved, and mixing them up is the single most common source of confusion in this project:

- **Laptop** (`C:\Users\leach\WhaleSweep`): Tim's development machine. Used for code changes, quick iteration, and any analysis that only needs the assets it has cached locally.
- **VPS** (`C:\Users\Administrator\WhaleSweep`): a separate clone that holds the real, full-history precomputed data and does all heavy search compute. Not reachable by a Claude session at all — every VPS action is Tim running a command himself and pasting the output back.

**The laptop's local data is a strict subset of the VPS's.** As of this writing the laptop has precomputed history for EURUSD (from 2024-02-27), GER40 (2022-06-07), XAUUSD (2022-04-13), and NDX100, all shorter than the VPS's ~10.24-year range (2016-05-27 to 2026-08-21). **GBPUSD, US30, SPX500, and AAPL have no precomputed data on the laptop at all.** Anything in this document computed from laptop data is explicitly flagged; anything touching those four assets could only be computed on the VPS and has not yet been re-run there under the latest fixes.

**Git discipline**: commits happen locally on the laptop clone only. Nobody working from a Claude session pushes to the remote — Tim always pushes himself, from whichever machine.

## 3. Pipeline

1. **`precompute.py`** — builds one `.parquet` per (asset, timeframe) from raw price data.
2. **`whale_sweep.py`** (via `start_search_4x.ps1`) — randomized parameter search over the strategy's ~25-dimension parameter space, run per-asset in parallel, checkpointed.
3. **`merge_search_results.py`** — merges the per-asset search outputs into one `top_strategies.json` candidate pool.
4. **`screen_all_candidates.py`** — runs each candidate through gate2 (out-of-sample holdout check), a plateau/robustness check, and a full historical replay, writing `candidate_screen.csv`.
5. **`portfolio_optimizer.py`** — takes every candidate that passed screening, dedups to one per (asset, session), computes each one's Kelly fraction from its real trade records, builds a cross-asset correlation matrix, and for every combo size from 2 to 6 searches for the risk multiplier `k` that lands the combo's historical pass rate near the 50% target, reporting the fastest (lowest median days-to-pass) combo at each size.
6. **`portfolio_report.py`** — a deep-dive on one already-chosen combo: trades/day, the full days-to-pass distribution, and pass-rate broken out over time (5yr/10yr/non-overlapping N-year buckets), as a robustness check on a fixed sizing rather than a re-optimization.

Every stage from step 2 onward reads an environment variable, `WS_HISTORY_YEARS` (default 2.0), that truncates how much history is actually used — this has been a repeated source of silent mistakes this project (see §7, "Known gotchas").

## 4. Major fixes made this session (2026-09-20)

### 4.1 Trading-cost modeling (commit `9e74308`)

**The problem**: WhaleSweep's original cost model was a free, unconstrained search parameter (`cost_atr_mult`) rather than a realistic fixed cost. The search was free to (and did) settle on zero cost for some candidates — two of the six legs in the pre-fix `DEFAULT_COMBO` were found with zero modeled trading cost.

**The fix**: replaced the free parameter with a fixed per-asset `COST_TABLE` (round-trip spread + commission, in the instrument's own price units), ported from a sister project (RCTBE) that live-measured these values against a real FTMO-Demo MT5 account. Values (round-trip cost in price units): XAUUSD 0.53, NDX100 1.83, SPX500 0.60, EURUSD 0.00007, US30 2.78, GER40 3.39, GBPUSD 0.00010. AAPL's value (0.30) is a rough, unverified estimate — RCTBE never traded stocks, so there's no measured figure to copy.

Fixing this required threading an `"asset"` key through several places that had silently dropped it (the search's own params dict, and two separate `_candidate_params()` helper copies used by every downstream script) — a real wiring bug found and fixed as a side effect of implementing the cost table.

**Result, verified against the real candidate pool (commit `2c486cb`)**: 108/110 candidates still pass gate2+plateau under real cost. But the risk multiplier `k` needed to hit the same 50% pass-rate target rose 1.3x to 3x depending on combo size — smaller/less-diversified combos need dramatically higher per-leg `risk_pct` once real cost is priced in, which directly worsens the margin picture below.

**Caveat carried forward**: this cost table has not been independently re-measured against WhaleSweep's own actual FTMO account — it's borrowed from RCTBE's account. Confirm same broker/account type before trusting it indefinitely.

### 4.2 Leverage / margin — not modeled at all, confirmed a real problem, and a promising fix direction found

**The gap**: a full code audit (grep for "leverage"/"margin"/"lot_size"/"contract_size"/"notional"/"position_size" across every script) turned up zero real hits. The entire challenge simulation works purely in already-realized dollar P&L space (`equity += risk_amt * r_multiple`) — it has no concept of a position being "open," tying up margin, between entry and exit. It cannot see whether a calculated position size is actually achievable within account equity at the instrument's real leverage cap, or whether several concurrently-open positions' combined margin would exceed the account.

**Why this matters for this specific portfolio**: every candidate that has survived screening so far sits at the same `session_start_minutes` (03:00 NY) — so positions from different legs aren't just landing on the same calendar day, they can be open at genuinely overlapping real clock times.

**Direct measurement** (on the 3-4 of 6 legs available locally; GBPUSD/SPX500/US30 need the VPS):
- 32-35% of new trade opens happen while at least one other leg is already open.
- Combined margin, time-weighted across the shared local window, exceeds **100% of account equity roughly 1-2% of trading time**, and 80% of equity roughly 1.5-2.8% of the time.
- A second, independent compounding factor: several individual candidates can have **more than one of their own positions open at once** (a real swept search parameter, `allow_level_rearm`, lets a candidate re-arm and re-trade a liquidity level before its prior trade on it closes). One candidate (NDX100/5min) does this on 41.9% of its own trades. This means even the single-leg margin estimates above are an undercount.

**A margin-gated re-simulation, and its result**: a standalone check was built that simply refuses to open any trade that would push combined margin (tracked in aggregate, in real clock time, across all legs — which automatically also captures the self-overlap effect above) past a fixed capacity. Re-running the exact same weekly-cohort FTMO simulation the real pipeline uses, on the same locally-available leg subsets, found:

| Combo (subset) | Cap | Trades rejected | Pass % | Median days-to-pass | p90 days-to-pass |
|---|---|---|---|---|---|
| New (4 of 6 legs) | none (baseline) | 0 / 3354 | 26.7% | 10 | 11 |
| New (4 of 6 legs) | 100% of equity | 462/3354 (13.8%) | 39.0% | 10 | 11 |
| New (4 of 6 legs) | 80% of equity | 689/3354 (20.5%) | 42.9% | 10 | 11 |
| New (4 of 6 legs) | 50% of equity | 1264/3354 (37.7%) | 48.6% | 11 | 16 |
| Old (3 of 6 legs) | none (baseline) | 0/1600 | 85.7% | 11 | 12 |
| Old (3 of 6 legs) | 100% of equity | 183/1600 (11.4%) | 88.6% | 11 | 12 |
| Old (3 of 6 legs) | 80% of equity | 277/1600 (17.3%) | 88.6% | 11 | 15 |
| Old (3 of 6 legs) | 50% of equity | 511/1600 (31.9%) | 93.3% | 11 | 18 |

**Margin-gating raises pass rate at every threshold tested.** The mechanism: essentially all historical challenge failures are `daily_loss` breaches (several positions stacking up and losing on the same day), not hard equity-floor breaches. Refusing a trade when the account is already near capacity blocks exactly the trade that would otherwise pile onto an already-losing day — every trade that *does* go through is completely unaffected (same entry, same exit, same P&L). This is a real, mechanistically-understood side effect of capping concurrent exposure, not a modeling artifact.

**The speed cost is small at loose caps, real at tight ones.** At a 100%-of-equity cap, days-to-pass is essentially untouched (median flat, only the extreme tail moves by a day). At 80%, the median holds but the tail (p90/max) starts stretching. Only at 50% does the median itself move (up about a day) and does p90 stretch meaningfully (5-6 days beyond baseline).

**Why you'd want a cap below 100% of equity, specifically:**

1. **A 100% cap is already the aggressive edge of the range, not a safe default.** The capacity check above is evaluated using the *static* margin required at trade-open. Real account equity is `balance + floating P&L`, and floating P&L moves against you before a stop-loss is ever hit. If you're sized right up against 100% of equity when a position opens, any adverse move on an open position reduces usable equity — you can hit a broker-side margin call or forced stop-out before WhaleSweep's own stop-loss logic would have closed the trade. A cap below 100% is the buffer for exactly that gap between "static margin required" and "actual live equity."
2. **It's the empirically better setting in this data, not just the safer one.** Pass rate keeps climbing as the cap tightens (39.0% → 42.9% → 48.6% for the new-combo subset, 100%→80%→50%), because a tighter cap more aggressively suppresses the correlated-loss-day failure mode that dominates historical failures. The tradeoff is a modest, now-quantified speed cost, not a pass-rate cost.
3. **Volatility/execution risk during exactly the clustered periods that matter.** The days multiple legs try to open together are disproportionately likely to be high-volatility, news-driven days — spreads widen and fills can require modestly more margin than the theoretical calculation assumes. A buffer absorbs that without breaching a hard cap.
4. **FTMO/broker margin-call and stop-out thresholds are not confirmed to sit exactly at 100% margin utilization** — many brokers use a lower forced-liquidation threshold. This hasn't been independently verified against FTMO's actual published terms (see open items below), which is itself a reason to build in headroom rather than assume 100% is the real ceiling.

**What this check is not, still**: it uses a binary skip-the-trade policy (reject outright), not partial position-size reduction, which would likely behave differently. It's a standalone script, not wired into `portfolio_optimizer.py` — the actual `risk_pct`/`k` values in use today were calibrated with zero margin awareness. If margin-gating became the real sizing rule, `k` could plausibly be pushed higher than today's calibration (since the worst same-day stacking would never get the chance to happen), for a bigger lift than shown above — that requires real code changes and a full re-derivation, not just this check. And it only covers 3-4 of 6 real legs; GBPUSD, SPX500, and US30 need the VPS to extend this properly, and can only add more overlap opportunities, not fewer.

## 5. Open decisions (need Tim's input, not a unilateral code change)

**How to actually fix the leverage/margin gap.** Three options were identified:

- **(a) Add real margin tracking to the simulation** and re-derive the risk multiplier `k`/pass-rate under an explicit margin constraint. Most correct, most work — and now the most attractive option given the margin-gate result above (real upside, modest cost at reasonable caps).
- **(b) Add a post-hoc audit/warning layer** without changing the core simulation. Cheaper, but doesn't capture the pass-rate upside found above, and doesn't actually fix sizing.
- **(c) Add an explicit notional/margin cap per trade** at the sizing stage. Addresses the root cause but changes what `risk_pct` means going forward.

Whichever is chosen should also account for the self-overlap effect (`allow_level_rearm`), not just cross-asset overlap, and should be validated against the full 6-leg portfolio (VPS) before treating any pass-rate number as final.

**Merging the 6-year search relaunch with the cost fix.** A separate, independent thread: the candidate pool currently in use (`top_strategies.json`) is still the original 2-year-window search. A 6-year relaunch was run to expose candidate discovery itself to a historically weaker 2022-2024 stretch (found via a bucketed pass-rate-over-time breakdown that showed a steady decline from 63.5% in 2016-2018 down to a 40.4% trough in 2022-2024, before recovering to ~52% in 2024-2026 — the same window `k` is fit against, so that recovery isn't independent confirmation of anything). That 6-year pool has not yet been merged, screened under real cost, and re-optimized end-to-end.

## 6. Known issues / limitations, summarized

- **Leverage/margin**: not modeled, confirmed materially real, a promising fix direction identified but not implemented (see §4.2, §5).
- **Trading cost**: fixed in code and verified against the real pool; not yet combined with the 6-year history relaunch.
- **Checkpoint/env-var blind spot**: `whale_sweep.py`'s checkpoint has no fingerprint of the environment variables (`WS_HISTORY_YEARS`, `WS_ASSETS`, etc.) that produced it, so resuming after changing one of those silently does zero new work with no warning. The same blind spot means nothing automatically catches a mismatch between a 2-year-window candidate pool and a 6-year-window screen — this has already had to be resolved by hand twice via file mtimes / git log.
- **No genuine trading-session diversity**: every surviving candidate sits at the same `session_start_minutes` (03:00 NY) — nothing at London or a true Asian session. This is directly relevant to the margin problem: real session diversity would spread positions across different clock times instead of stacking them, reducing concurrent-margin risk as a side effect.
- **Correlation clusters**: roughly 22 candidates across GER40/EURUSD/GBPUSD/XAUUSD were found sharing the exact same worst-window start date and cohort count in an earlier pass — likely one shared market regime showing up across correlated markets rather than independent edge. `avg_pairwise_corr` is always reported alongside any combo for this reason.
- **Kelly modeling**: Kelly fractions here are used as a relative cross-asset weighting heuristic, not literal growth-optimal sizing, since the actual FTMO simulation uses fixed-dollar risk with no compounding.
- **Laptop data gap**: GBPUSD, US30, SPX500, and AAPL have no local precomputed data at all — any number touching those assets in this document required the VPS and has not been independently re-verified locally.

## 7. Known gotchas worth remembering

- **PowerShell env vars**: use `$env:VAR="value"`, never `set VAR=value` — the latter is cmd.exe syntax and silently sets nothing PowerShell/Python will see.
- **Checkpoint + env var changes**: always use a fresh/renamed output directory when changing any search-affecting env var, or a relaunch can silently resume into a stale completed checkpoint and do zero new work.
- **`candidate_screen.csv`/`portfolio_optimizer.csv` showing "modified" right after a fresh git pull** is very likely a harmless CRLF/LF line-ending artifact, not a real content change — verify with a keyed row comparison (asset+tf+local_rank) before assuming otherwise.
- **Never `git push` from a Claude session** — commits happen locally on the laptop clone; Tim pushes himself.

## 8. Appendix — commit reference

- `d333204` — fix `k_grid` range bug in `portfolio_optimizer.py` causing missed risk-level crossings.
- `c1d124b` — add `portfolio_report.py` (deep-dive on a chosen combo).
- `7bff3bf` — `portfolio_report.py`: print effective `HISTORY_YEARS` + raw parquet range.
- `042054f` — `portfolio_report.py`: add non-overlapping time-bucket breakdown.
- `9e74308` — replace the free-swept `cost_atr_mult` with a real per-asset FTMO cost table; fixes the `"asset"`-wiring gap this required.
- `2c486cb` (VPS) — re-screen of the (still 2-year-window) candidate pool at real FTMO cost.

The leverage/margin investigation and the margin-gated re-simulation (this document's §4.2) are research/advisory only as of this writing — no code changes, no commits.
