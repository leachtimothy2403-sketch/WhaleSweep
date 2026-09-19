#!/usr/bin/env python3
"""
WhaleSweep -- portfolio optimizer (2026-09-19, per Tim).

Picks a small set of DECORRELATED (asset, session) candidates from an
already-screened pool, Kelly-weights each one's risk per trade from its
own historical R-multiple distribution, then finds the single global
risk-scaling multiplier that makes the resulting multi-asset FTMO
account's OVERALL historical average pass rate land at approximately
Tim's target (default 50%) -- reporting, for each portfolio size tried,
the combination with the FASTEST median calendar-days-to-pass among
those hitting that target, alongside its correlation profile so a fast
answer is never silently also a correlated (single-regime) one.

Why this exists: screen_all_candidates.py's own first real run (see its
docstring/README.md's "2026-09-19: trade-frequency overhaul" section)
surfaced a cluster of ~22 candidates across 4 different assets that all
shared the exact same worst-window start date and cohort count -- almost
certainly one shared market regime showing up across correlated
markets, not 22 independent proofs of edge. Treating any subset of that
cluster as "diversification" would be a mistake. This script makes
decorrelation an explicit, measured selection criterion instead of an
afterthought.

IMPORTANT FINDING (2026-09-19, read before trusting "session" output):
every one of the 110 real candidates in the merged top_strategies.json
has session_start_minutes in {180, 300} (03:00 or 05:00 NY) -- NONE at
420 (07:00) or 570 (09:30, the old NY-cash-open anchor), and the search
space has no way to express a genuine Asian-session (18:00-03:00 NY,
crosses midnight) ENTRY window at all -- see whale_sweep.py's SPACE and
generate_signals()'s per-calendar-day windowing, which cannot span two
calendar days. In practice this means the "NY session" and "Asian
session" buckets Tim asked to diversify across are EMPTY in the current
candidate pool -- the search converged entirely on early/pre-London-ish
starts, almost certainly because a wider trailing session window (up to
16:00 NY) scores higher under the new frequency-aware accept gate.
Session diversification is reported honestly (grouped by the ACTUAL
session_start_minutes value seen) rather than faked into three buckets
that don't exist in the data. See this script's own README note in
WhaleSweep's repo for how to get real cross-session diversity later
(constrain session_start_minutes per search run, or add a true
midnight-spanning Asian entry window to generate_signals()).

MODELING CAVEATS (read before sizing real risk):
- Kelly here maximizes E[log(1+f*R)] on each candidate's OWN historical
  R-multiples -- the classical growth-optimal fraction ASSUMING
  compounding (bet size proportional to CURRENT capital). This
  project's FTMO simulation (run_phase) uses FIXED-DOLLAR risk pegged
  to the challenge's STARTING equity throughout (no compounding) --
  reasonable for a short (weeks-long) challenge, but not textbook
  Kelly's own assumption. Kelly fractions here are used as a RELATIVE
  weighting heuristic across assets (more edge/less variance -> more
  allocated risk), not a literal growth-optimal derivation for this
  specific fixed-risk, hard-floor-constrained environment. The GLOBAL
  scaling multiplier (found by direct simulation against FTMO's real
  daily-loss/max-loss floors) is what actually calibrates absolute
  risk -- that part IS empirically grounded, not theoretical.
- Same overlapping-cohort caveat as candidate_report.py's replay: not a
  day-block-bootstrap Monte Carlo. A first-pass sanity read.
- The "hits ~50% overall pass rate" search can find TWO risk levels
  (pass rate is not monotonic in risk -- too little risk mostly never
  reaches target within the window; too much blows the daily-loss/
  max-loss floor before reaching target) -- this script evaluates a
  log-spaced grid, refines every crossing of the target found, and
  reports median days-to-pass at EACH one it finds (not just the first)
  so you can see which side of the curve you're actually choosing.

Usage:
    py -3 portfolio_optimizer.py
    py -3 portfolio_optimizer.py --challenge 2step --target-pass-pct 50
    py -3 portfolio_optimizer.py --max-size 5 --min-ww-n 30
"""
import argparse
import itertools
import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd

import whale_sweep as ws
import gate2_holdout as g2
import ftmo_challenge_rules as ftmo

START_EQUITY = ftmo.START_EQUITY


# ══════════════════════════════════════════════════════════════════════════
#  Loading + filtering + dedup
# ══════════════════════════════════════════════════════════════════════════

def load_candidate_pool(screen_csv: str, top_json: str) -> list[dict]:
    """Joins candidate_screen.csv's gate2/plateau/worst-window verdicts
    back onto their full parameter rows in top_json, using the EXACT
    same (asset, entry_timeframe) file-order local_rank convention
    screen_all_candidates.py's own main() uses -- a mismatch here would
    silently attach the wrong params to a candidate's verdicts."""
    with open(top_json, encoding="utf-8") as f:
        all_rows = json.load(f)
    per_key_rows: dict[tuple[str, str], list[dict]] = {}
    for r in all_rows:
        per_key_rows.setdefault((r["asset"], r["entry_timeframe"]), []).append(r)

    screen = pd.read_csv(screen_csv)
    pool = []
    for _, srow in screen.iterrows():
        key = (srow["asset"], srow["tf"])
        rows = per_key_rows.get(key)
        if rows is None or srow["local_rank"] >= len(rows):
            continue
        full_row = rows[int(srow["local_rank"])]
        pool.append({**full_row, **srow.to_dict(),
                     "asset": key[0], "entry_timeframe": key[1]})
    return pool


def filter_and_dedup(pool: list[dict], min_ww_n: int) -> list[dict]:
    """Gate2 PASS + plateau PASS only, then keep a single candidate per
    (asset, session_start_minutes) -- Tim's own instinct ("single
    candidate per asset and per session"). session_start_minutes IS the
    session label here (see this file's module docstring for why the
    real pool only has two distinct values, not three)."""
    survivors = [r for r in pool if r.get("gate2_verdict") == "PASS" and r.get("plateau_verdict") == "PASS"]
    if min_ww_n > 0:
        survivors = [r for r in survivors
                     if r.get("worst_window_n") is not None and r["worst_window_n"] >= min_ww_n]

    def _quality_key(r):
        ww_n = r.get("worst_window_n") or 0
        ww_pct = r.get("worst_window_pass_pct")
        overall = r.get("overall_pass_pct") or 0.0
        return (ww_n >= 30, ww_pct if ww_pct is not None else -1.0, overall)

    best_per_slot: dict[tuple, dict] = {}
    for r in survivors:
        slot = (r["asset"], r.get("session_start_minutes"))
        if slot not in best_per_slot or _quality_key(r) > _quality_key(best_per_slot[slot]):
            best_per_slot[slot] = r
    return list(best_per_slot.values())


# ══════════════════════════════════════════════════════════════════════════
#  Per-candidate trade records, Kelly fraction, daily return series
# ══════════════════════════════════════════════════════════════════════════

def _kelly_fraction(r_multiples: np.ndarray, f_max_cap: float = 2.0) -> float:
    """argmax_f mean(log(1 + f*R)) via golden-section search -- concave
    on its valid domain, so this is safe without an external optimizer
    dependency. Upper bound keeps 1+f*R_min > 0 (no log of a
    non-positive number) with headroom, and additionally caps at
    f_max_cap (default 200% notional-equivalent) as a sanity backstop
    against a tiny/noisy sample producing an absurd fraction."""
    r_min = r_multiples.min()
    f_hi = min(f_max_cap, 0.999 * (-1.0 / r_min) if r_min < 0 else f_max_cap)
    f_lo = 1e-6
    if f_hi <= f_lo:
        return f_lo

    def neg_obj(f):
        return -np.mean(np.log1p(f * r_multiples))

    gr = (np.sqrt(5) - 1) / 2
    a, b = f_lo, f_hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    for _ in range(60):
        if neg_obj(c) < neg_obj(d):
            b = d
        else:
            a = c
        c, d = b - gr * (b - a), a + gr * (b - a)
    return float((a + b) / 2)


def build_candidate_data(row: dict, df_cache: dict) -> dict:
    """Real trade records, Kelly fraction, and a day -> ordered list of
    (timestamp, r_multiple) series (for merging across assets and for
    daily-correlation computation)."""
    key = (row["asset"], row["entry_timeframe"])
    if key not in df_cache:
        df_cache[key] = ws.load_precomputed(*key)
    df = df_cache[key]
    p = g2._candidate_params(row)
    records = ws.get_trade_records(df, p)

    trades = []
    for r in records:
        ts = df.index[r["entry_idx"]]
        trades.append((ts, float(r["r_multiple"])))
    trades.sort(key=lambda t: t[0])

    r_arr = np.array([r for _, r in trades], dtype=float)
    kelly_f = _kelly_fraction(r_arr) if len(r_arr) >= 30 else 0.0

    daily = defaultdict(float)
    for ts, r in trades:
        daily[ts.date()] += r
    daily_series = pd.Series(daily, name=f"{row['asset']}_{row['entry_timeframe']}_{row.get('session_start_minutes')}")

    return {"label": f"{row['asset']}/{row['entry_timeframe']} ss={row.get('session_start_minutes')} "
                     f"(local_rank={row.get('local_rank')})",
            "asset": row["asset"], "trades": trades, "kelly_f": kelly_f,
            "n_trades": len(trades), "daily_series": daily_series}


def correlation_matrix(cand_data: list[dict]) -> pd.DataFrame:
    df = pd.concat([c["daily_series"] for c in cand_data], axis=1).fillna(0.0)
    return df.corr()


# ══════════════════════════════════════════════════════════════════════════
#  Multi-asset FTMO simulation for one combination at one risk multiplier
# ══════════════════════════════════════════════════════════════════════════

def _merge_by_date_dollars(combo: list[dict], k: float) -> tuple[dict, list]:
    """Pre-scales each candidate's trades by ITS OWN dollar risk amount
    (risk_pct = k * that candidate's Kelly fraction), merges all of
    them into one chronologically-ordered stream, then buckets by
    calendar date -- so a mid-day floor breach from one asset correctly
    cuts off that SAME day's later trades from every other asset too,
    matching what a real single shared FTMO account would do."""
    all_entries = []
    for c in combo:
        risk_amt = START_EQUITY * (k * c["kelly_f"])
        all_entries.extend((ts, risk_amt * r) for ts, r in c["trades"])
    all_entries.sort(key=lambda e: e[0])

    by_date = defaultdict(list)
    for ts, val in all_entries:
        by_date[ts.date()].append(val)
    all_days = sorted(by_date.keys())
    return dict(by_date), all_days


def simulate_portfolio(combo: list[dict], k: float, challenge: str) -> dict:
    by_date, all_days = _merge_by_date_dollars(combo, k)
    if len(all_days) < 8:
        return {"n_cohorts": 0}
    mondays = ftmo.get_mondays_full(all_days)
    sim_fn = ftmo.simulate_2step_portfolio if challenge == "2step" else ftmo.simulate_1step_portfolio
    outcomes = [sim_fn(by_date, all_days, start) for start in mondays]
    n = len(outcomes)
    n_pass = sum(1 for o in outcomes if o["outcome"] == "PASS")
    n_fail = sum(1 for o in outcomes if o["outcome"] == "FAIL")
    pass_days = sorted(o["calendar_days"] for o in outcomes if o["outcome"] == "PASS" and o["calendar_days"])
    med_days = pass_days[len(pass_days) // 2] if pass_days else None
    return {"n_cohorts": n, "n_pass": n_pass, "n_fail": n_fail,
            "pass_pct": 100.0 * n_pass / n if n else None,
            "still_going_pct": 100.0 * (n - n_pass - n_fail) / n if n else None,
            "median_days_to_pass": med_days}


def find_target_risk_levels(combo: list[dict], challenge: str, target_pct: float,
                             tol_pct: float, k_grid: np.ndarray) -> list[dict]:
    """Evaluates the k-grid, finds every sign change of (pass_pct -
    target) across consecutive grid points, and bisection-refines each
    one -- pass_pct vs risk is not monotonic (see module docstring), so
    there can legitimately be zero, one, or two crossings."""
    evals = []
    for k in k_grid:
        res = simulate_portfolio(combo, k, challenge)
        evals.append((k, res))

    crossings = []
    for (k_lo, res_lo), (k_hi, res_hi) in zip(evals, evals[1:]):
        p_lo, p_hi = res_lo.get("pass_pct"), res_hi.get("pass_pct")
        if p_lo is None or p_hi is None:
            continue
        if (p_lo - target_pct) == 0:
            crossings.append((k_lo, res_lo))
            continue
        if (p_lo - target_pct) * (p_hi - target_pct) < 0:
            lo, hi = k_lo, k_hi
            for _ in range(25):
                mid = (lo + hi) / 2
                res_mid = simulate_portfolio(combo, mid, challenge)
                p_mid = res_mid.get("pass_pct")
                if p_mid is None:
                    break
                if abs(p_mid - target_pct) <= tol_pct:
                    crossings.append((mid, res_mid))
                    break
                if (p_lo - target_pct) * (p_mid - target_pct) < 0:
                    hi = mid
                else:
                    lo, p_lo = mid, p_mid
            else:
                crossings.append((mid, res_mid))
    return [{"k": k, **res} for k, res in crossings]


# ══════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen-csv", default="whale_sweep_output/candidate_screen.csv")
    ap.add_argument("--top-json", default="whale_sweep_output/top_strategies.json")
    ap.add_argument("--challenge", choices=["1step", "2step"], default="2step")
    ap.add_argument("--target-pass-pct", type=float, default=50.0)
    ap.add_argument("--tol-pct", type=float, default=3.0)
    ap.add_argument("--min-ww-n", type=int, default=0,
                     help="Drop candidates whose worst-window sample is thinner than this (default: no filter).")
    ap.add_argument("--max-size", type=int, default=6, help="Largest portfolio size to search.")
    ap.add_argument("--max-combos", type=int, default=4000,
                     help="Safety cap on total combinations evaluated across all sizes.")
    ap.add_argument("--min-risk-pct", type=float, default=0.0002,
                     help="Lower end of the average per-trade risk_pct swept per combo (default 0.02%%).")
    ap.add_argument("--max-risk-pct", type=float, default=0.10,
                     help="Upper end of the average per-trade risk_pct swept per combo (default 10%%).")
    ap.add_argument("--k-grid-points", type=int, default=30)
    ap.add_argument("--out-csv", default="whale_sweep_output/portfolio_optimizer.csv")
    args = ap.parse_args()

    pool_raw = load_candidate_pool(args.screen_csv, args.top_json)
    pool = filter_and_dedup(pool_raw, args.min_ww_n)
    print(f"{len(pool_raw)} screened candidates -> {len(pool)} after gate2+plateau filter and "
          f"single-candidate-per-(asset,session) dedup.")
    sessions_seen = sorted(set(r.get("session_start_minutes") for r in pool))
    print(f"session_start_minutes values represented: {sessions_seen} "
          f"(see this script's module docstring if this list is missing 420/570/an Asian entry window)")
    for r in sorted(pool, key=lambda r: (r["asset"], r.get("session_start_minutes"))):
        print(f"  {r['asset']:8s} {r['entry_timeframe']:5s} ss={r.get('session_start_minutes')!s:5s} "
              f"local_rank={r['local_rank']:>2} ww_pct={r.get('worst_window_pass_pct')} "
              f"ww_n={r.get('worst_window_n')} overall_pct={r.get('overall_pass_pct')}")

    if len(pool) < 2:
        raise SystemExit("Fewer than 2 candidates survived filtering -- nothing to build a portfolio from.")

    print("\nComputing real trade records, Kelly fractions, and daily return series per candidate...")
    df_cache = {}
    cand_data = []
    for row in pool:
        try:
            cd = build_candidate_data(row, df_cache)
        except FileNotFoundError:
            print(f"  SKIP {row['asset']}/{row['entry_timeframe']} -- precomputed file not found here")
            continue
        cand_data.append(cd)
        print(f"  {cd['label']}: {cd['n_trades']} trades, kelly_f={cd['kelly_f']:.4f}")

    corr = correlation_matrix(cand_data)
    print("\nPairwise daily-return correlation matrix:")
    print(corr.round(2).to_string())

    n = len(cand_data)
    results = []
    combos_tried = 0
    t0 = time.time()
    for size in range(2, min(args.max_size, n) + 1):
        for combo_idx in itertools.combinations(range(n), size):
            if combos_tried >= args.max_combos:
                break
            combos_tried += 1
            combo = [cand_data[i] for i in combo_idx]
            pair_corrs = [corr.iloc[i, j] for a, i in enumerate(combo_idx) for j in combo_idx[a + 1:]]
            avg_corr = float(np.mean(pair_corrs)) if pair_corrs else None
            # 2026-09-19 fix: a single fixed absolute k range missed real
            # crossings for combos with more assets / higher raw Kelly
            # fractions -- aggregate daily risk scales with both, so the
            # k where the pass-rate cliff happens shrinks as combos get
            # bigger (confirmed directly: a 4-candidate combo's pass rate
            # was 98% at k=0.02 and 45% at k=0.05, so a grid starting AT
            # 0.05 never had a point on the high side to bracket against
            # at all). Scale the grid by this combo's OWN mean Kelly
            # fraction instead, so it always sweeps the same MEANINGFUL
            # range of average per-trade risk_pct (0.02% to 10%) no
            # matter how many assets or how aggressive their raw Kelly
            # fractions are.
            mean_kelly = float(np.mean([c["kelly_f"] for c in combo]))
            k_grid = np.geomspace(args.min_risk_pct / mean_kelly, args.max_risk_pct / mean_kelly, args.k_grid_points)
            crossings = find_target_risk_levels(combo, args.challenge, args.target_pass_pct, args.tol_pct, k_grid)
            for cr in crossings:
                results.append({
                    "size": size, "assets": "+".join(sorted(c["asset"] for c in combo)),
                    "labels": " | ".join(c["label"] for c in combo),
                    "avg_pairwise_corr": round(avg_corr, 3) if avg_corr is not None else None,
                    "risk_multiplier_k": round(cr["k"], 4),
                    "pass_pct": round(cr.get("pass_pct", 0.0), 1),
                    "still_going_pct": round(cr.get("still_going_pct", 0.0), 1),
                    "median_days_to_pass": cr.get("median_days_to_pass"),
                    "n_cohorts": cr.get("n_cohorts"),
                    "per_asset_risk_pct": {c["asset"]: round(float(100 * cr["k"] * c["kelly_f"]), 3) for c in combo},
                })
        if combos_tried >= args.max_combos:
            print(f"  hit --max-combos={args.max_combos} cap, stopping the search early")
            break

    print(f"\nEvaluated {combos_tried} combinations in {time.time() - t0:.0f}s, "
          f"found {len(results)} risk levels landing near {args.target_pass_pct}% pass rate.")

    if not results:
        print("No combination reached the target pass rate within tolerance at any grid point -- "
              "try widening --tol-pct, changing --target-pass-pct, or checking --min-ww-n isn't over-filtering.")
        return

    results_df = pd.DataFrame(results)
    results_df.to_csv(args.out_csv, index=False)
    print(f"Full results written to {args.out_csv}\n")

    print("Fastest combination found for EACH portfolio size (median calendar days to pass, among "
          f"results within {args.tol_pct}pp of the {args.target_pass_pct}% target):")
    for size, group in results_df.groupby("size"):
        best = group.sort_values("median_days_to_pass", na_position="last").iloc[0]
        print(f"\n  size={size}  assets={best['assets']}  avg_corr={best['avg_pairwise_corr']}  "
              f"pass%={best['pass_pct']}  median_days_to_pass={best['median_days_to_pass']}  "
              f"risk_multiplier_k={best['risk_multiplier_k']}")
        print(f"    {best['labels']}")
        print(f"    per-asset risk_pct at this k: {best['per_asset_risk_pct']}")

    print("\nRead the avg_pairwise_corr column as seriously as the speed number: a fast combination "
          "built from highly correlated candidates (see this script's module docstring on the "
          "2024-08-26 cluster) is one bad regime away from failing them all at once, not genuine "
          "diversification.")


if __name__ == "__main__":
    main()
