#!/usr/bin/env python3
"""
WhaleSweep — cheap plumbing self-test (same convention as
../MeanReversion/selftest.py). NOT a strategy validation — just checks
the pipeline is wired correctly: precomputed files load, every
confirmation_mode produces internally-consistent trades (SL on the
correct side of entry, R-multiple sign matches outcome), the search
harness's scoring function runs, and ftmo_challenge_rules.py still
simulates without error. Run this after any change to precompute.py or
whale_sweep.py before trusting a long unattended search.

Usage: py -3 selftest.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import whale_sweep as ws
import ftmo_challenge_rules as ftmo


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def check_data_available() -> list[tuple[str, str]]:
    pairs = ws.discover_asset_timeframes()
    if not pairs:
        _fail("no ws_precomputed_*.parquet files found — run precompute.py first")
    print(f"OK  found {len(pairs)} precomputed (asset, timeframe) files: {pairs}")
    return pairs


def check_signal_invariants(asset: str, tf: str) -> None:
    df = ws.load_precomputed(asset, tf)
    base = dict(
        asset=asset, entry_timeframe=tf, atr_window=14, include_secondary_levels=True,
        # 2026-09-19: exercise both new toggles (not just default them away)
        # so this selftest actually covers the re-arm state machine and the
        # new session-level lookup, not just the pre-existing PDH/PDL path.
        allow_level_rearm=True, include_session_levels=True,
        close_beyond_lookback_bars=3, wick_atr_mult=1.0, reversal_lookback_bars=5,
        reversal_requires_close=True, bos_lookback_bars=8, bos_mode="fractal", bos_confirm_atr_mult=0.1,
        require_rsi_confirm=False, rsi_period=14, rsi_mode="overbought_oversold", rsi_ob=70, rsi_os=30,
        sl_atr_buffer_mult=0.3, sl_extend_to_next_level=True, sl_extend_check_atr_mult=1.0,
        tp_mode="fixed_rr", rr=1.5, session_start_minutes=570, session_end_minutes=690,
        max_trades_per_day=3, skip_weekday=-1,
        # cost_atr_mult removed 2026-09-20 -- real per-asset cost now comes
        # from whale_sweep_cost_table.COST_TABLE via p["asset"] instead.
    )
    total, bad = 0, 0
    for mode in ws.CONFIRMATION_MODES:
        p = dict(base, confirmation_mode=mode)
        for bos_mode in (["fractal", "raw_wick"] if mode == "break_of_structure" else [None]):
            if bos_mode:
                p = dict(p, bos_mode=bos_mode)
            sigs = ws.generate_signals(df, p)
            res = ws.backtest_signals(sigs, df)
            for r in res:
                total += 1
                if r["direction"] == "Sell" and r["sl"] <= r["entry"]:
                    bad += 1
                if r["direction"] == "Buy" and r["sl"] >= r["entry"]:
                    bad += 1
                if r["risk"] <= 0 or np.isnan(r["risk"]):
                    bad += 1
                # 2026-09-20 cost fix: real per-asset cost can now exceed a
                # very tight stop's raw R (confirmed live on EURUSD/1min --
                # cost=0.00007, some SL distances as small as 0.000037, so a
                # TP hit can still net r_multiple < 0). That's realistic, not
                # a bug, so "TP means r>=0"/"SL means r<=0.01" are no longer
                # valid invariants on their own. Directly recompute what
                # backtest_signals() itself should have produced from
                # entry/exit/cost/risk (same formula as that function) and
                # check it matches, instead of assuming a sign.
                raw_pnl = (r["entry"] - r["exit_price"]) if r["direction"] == "Sell" else (r["exit_price"] - r["entry"])
                expected_r = (raw_pnl - r["cost"]) / r["risk"] if r["risk"] > 0 else 0.0
                if abs(r["r_multiple"] - expected_r) > 1e-4:
                    bad += 1
    if bad:
        _fail(f"{bad}/{total} trades violated an SL/outcome invariant on {asset}/{tf}")
    print(f"OK  {asset}/{tf}: {total} trades across all confirmation_modes, 0 invariant violations")


def check_multiperiod(asset: str, tf: str) -> None:
    df = ws.load_precomputed(asset, tf)
    p = ws.sample_params()
    p["asset"] = asset
    p["entry_timeframe"] = tf
    result = ws.backtest_multiperiod(df, p)
    print(f"OK  backtest_multiperiod ran on {asset}/{tf} (result={'None (too few trades)' if result is None else 'dict with score=' + str(result['score'])})")


def check_ftmo_rules() -> None:
    rng = np.random.default_rng(42)
    days = pd.date_range("2022-01-03", periods=500, freq="B")
    by_date = {d: rng.normal(0.05, 1.0, size=rng.integers(1, 4)) for d in days}
    r1 = ftmo.simulate_1step(by_date, list(days), days[0], risk_pct=0.005)
    r2 = ftmo.simulate_2step(by_date, list(days), days[0], risk_pct=0.005)
    assert r1["outcome"] in ("PASS", "FAIL", "STILL_GOING")
    assert r2["outcome"] in ("PASS", "FAIL", "STILL_GOING")
    mondays = ftmo.get_mondays_full(list(days))
    assert len(mondays) > 0
    print(f"OK  ftmo_challenge_rules: 1-step={r1['outcome']}, 2-step={r2['outcome']}, "
          f"{len(mondays)} weekly cohort anchors")


def main() -> None:
    pairs = check_data_available()
    for asset, tf in pairs:
        check_signal_invariants(asset, tf)
        check_multiperiod(asset, tf)
    check_ftmo_rules()
    print("\nALL SELFTESTS PASSED")


if __name__ == "__main__":
    main()
