#!/usr/bin/env python3
"""
WhaleSweep -- one-off: extract a full trade log (+ surrounding OHLC context
for charting, + the swept liquidity level's own price) for one specific
screened candidate, restricted to the most recent N_MONTHS full calendar
months present in the local precomputed data.

Answers Tim's 2026-09-23 request for an HTML trade report on the
NDX100/3min ss=420 (local_rank=6) candidate -- the fourth leg of the
size=4 combo from portfolio_optimizer.csv -- extended on request to
(a) show the actual PDH/PDL/etc level that was swept on each chart and
(b) cover the last 3 full months instead of just 1.

NOTE ON DATA WINDOW: this reads whale_sweep.py's load_precomputed(), which
on this laptop is truncated to WS_HISTORY_YEARS (default 2.0, NOT the
VPS's full 6-year history). That's fine for THIS specific request --
we only want the most recent few months, identical whether viewed
through a 2-year or 6-year window (truncation only drops OLDER data).
It would NOT be fine for anything needing the full 6-year sample.

Run from the WhaleSweep repo root:
    py -3 trade_report_extract.py
"""
import json

import numpy as np
import pandas as pd

import gate2_holdout as g2
import portfolio_optimizer as po
import whale_sweep as ws
from ftmo_challenge_rules import START_EQUITY

ASSET, TF, SS, LOCAL_RANK = "NDX100", "3min", 420, 6
K = 0.0677  # calibrated value portfolio_optimizer.py found for the size=4 combo containing this leg
CONTEXT_BARS_BEFORE = 20
CONTEXT_BARS_AFTER = 10
N_MONTHS = 3
OUT_PATH = "trade_report_data.json"

LEVEL_COL = {
    "PDH": "pdh", "PDL": "pdl", "PDH2": "pdh2", "PDL2": "pdl2",
    "SWING_HIGH_ABOVE_PDH": "swing_high_above_pdh", "SWING_LOW_BELOW_PDL": "swing_low_below_pdl",
    "ASIAN_HIGH": "asian_high", "ASIAN_LOW": "asian_low",
    "LONDON_HIGH": "london_high", "LONDON_LOW": "london_low",
    "PREV_WEEK_HIGH": "prev_week_high", "PREV_WEEK_LOW": "prev_week_low",
}

pool = po.load_candidate_pool("whale_sweep_output/candidate_screen.csv", "whale_sweep_output/top_strategies.json")
row = next(r for r in pool if r["asset"] == ASSET and r["entry_timeframe"] == TF
           and r.get("session_start_minutes") == SS and r["local_rank"] == LOCAL_RANK)

df_cache = {}
cd = po.build_candidate_data(row, df_cache)
kelly_f = cd["kelly_f"]
risk_amt = START_EQUITY * (K * kelly_f)

p = g2._candidate_params(row)
df = ws.load_precomputed(ASSET, TF)
records = ws.get_trade_records(df, p)

# first bar index of each calendar day, keyed by day_id string -- levels
# (PDH/PDL/etc) are fixed at day-start (row0 in generate_signals), so this
# reproduces the exact value the strategy tested against.
day_id_arr = df["day_id"].to_numpy()
day_start_idx = {}
for idx, d in enumerate(day_id_arr):
    if d not in day_start_idx:
        day_start_idx[d] = idx

data_start, data_end = df.index[0], df.index[-1]
print(f"Local precomputed data range (UTC): {data_start} .. {data_end}")

period_containing_end = data_end.to_period("M")
is_full = data_end >= (period_containing_end.end_time.tz_localize("UTC") - pd.Timedelta(days=1))
latest_full_month = period_containing_end if is_full else period_containing_end - 1
first_month = latest_full_month - (N_MONTHS - 1)
month_start = first_month.start_time.tz_localize("UTC")
month_end_excl = (latest_full_month + 1).start_time.tz_localize("UTC")
print(f"Selected {N_MONTHS} latest FULL months: {first_month} .. {latest_full_month} "
      f"-> [{month_start}, {month_end_excl})")

CEST = "Europe/Berlin"  # CEST/CET with correct DST transitions

trades_out = []
for r in records:
    entry_ts = df.index[r["entry_idx"]]
    if not (month_start <= entry_ts < month_end_excl):
        continue
    exit_ts = df.index[r["exit_idx"]]
    dollar_pnl = float(r["r_multiple"]) * risk_amt

    level_col = LEVEL_COL.get(r["level"])
    row0 = day_start_idx.get(r["day_id"])
    level_value = float(df[level_col].iloc[row0]) if (level_col and row0 is not None) else None

    lo = max(0, r["entry_idx"] - CONTEXT_BARS_BEFORE)
    hi = min(len(df) - 1, r["exit_idx"] + CONTEXT_BARS_AFTER)
    ctx = df.iloc[lo:hi + 1]
    context = [{
        "ts_utc": ts.isoformat(),
        "ts_cest": ts.tz_convert(CEST).isoformat(),
        "open": float(row_["open"]), "high": float(row_["high"]),
        "low": float(row_["low"]), "close": float(row_["close"]),
    } for ts, row_ in ctx.iterrows()]

    trades_out.append({
        "day_id": r["day_id"], "direction": r["direction"], "level": r["level"],
        "level_value": level_value,
        "entry_ts_utc": entry_ts.isoformat(), "entry_ts_cest": entry_ts.tz_convert(CEST).isoformat(),
        "exit_ts_utc": exit_ts.isoformat(), "exit_ts_cest": exit_ts.tz_convert(CEST).isoformat(),
        "entry_price": float(r["entry"]), "sl": float(r["sl"]), "tp": float(r["tp"]),
        "risk_points": float(r["risk"]), "outcome": r["outcome"],
        "exit_price": float(r["exit_price"]), "r_multiple": float(r["r_multiple"]),
        "dollar_pnl": dollar_pnl, "entry_idx": int(r["entry_idx"]), "exit_idx": int(r["exit_idx"]),
        "sweep_idx": int(r["sweep_idx"]),
        "context": context,
    })

trades_out.sort(key=lambda t: t["entry_ts_utc"])
print(f"Trades in [{first_month}..{latest_full_month}]: {len(trades_out)}")
n_missing_level = sum(1 for t in trades_out if t["level_value"] is None)
if n_missing_level:
    print(f"WARNING: {n_missing_level} trades missing a resolved level value (unmapped level name / missing column)")

out = {
    "asset": ASSET, "tf": TF, "session_start_minutes": SS, "local_rank": LOCAL_RANK,
    "k_used": K, "kelly_f": kelly_f, "risk_amt_dollars": risk_amt,
    "start_equity": START_EQUITY,
    "data_range_utc": [data_start.isoformat(), data_end.isoformat()],
    "months_covered": [str(first_month), str(latest_full_month)],
    "history_window_note": "Laptop precomputed data is WS_HISTORY_YEARS-truncated (2yr default), "
                            "not the VPS's full 6yr history -- fine for a few recent months "
                            "since truncation only drops older data.",
    "trades": trades_out,
}
with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)
print(f"Wrote {OUT_PATH} ({len(trades_out)} trades)")
