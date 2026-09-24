#!/usr/bin/env python3
"""
MNQ/5min fixed_rr candidate (the one from the 2026-09-23 Tradeify work):
FundedNext Flex $100k vs Tradeify Growth $100k, full 6-year weekly cohorts.
    WS_HISTORY_YEARS=6 py -3 mnq_fundednext_vs_tradeify.py
"""
import pandas as pd

import whale_sweep as ws
import fundednext_flex_rules as fx

TF = "5min"
results = pd.read_csv("whale_sweep_output_mnq_6yr/results.csv")
fixed = results[(results["tp_mode"] == "fixed_rr") & (results["entry_timeframe"] == TF)]
row = fixed[(fixed["sl_atr_buffer_mult"] == 0.75) & (fixed["rr"] == 1.0)].sort_values("score", ascending=False).iloc[0]
p = {k: row[k] for k in ws.SPACE.keys() if k in row.index}
for k in ws.SPACE.keys():
    if k not in p or (isinstance(p[k], float) and pd.isna(p[k])):
        p[k] = ws.SPACE[k][0]
p["asset"] = "MNQ"
df = ws.load_precomputed("MNQ", TF)
rec = ws.get_trade_records(df, p)
print(f"MNQ/{TF} fixed_rr PF={row['profit_factor']:.2f} win={row['win_rate_pct']:.1f}%  "
      f"{len(rec)} trades {df.index[0].date()} .. {df.index[-1].date()}")

rows = []
for risk in (500, 750, 1000, 1500, 2000):
    rows.append({"account": "Tradeify Growth", "risk$": risk, **fx.simulate_tradeify(rec, df, risk)})
    for md in (1, 3, 5):
        rows.append({"account": f"FN Flex min{md}d", "risk$": risk, **fx.simulate_flex(rec, df, risk, min_days=md)})
    rows.append({"account": "FN Flex min1d lock@100k", "risk$": risk,
                 **fx.simulate_flex(rec, df, risk, min_days=1, lock_level=100_000.0)})
t = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
print(t.to_string(index=False))
t.to_csv("mnq_fundednext_vs_tradeify.csv", index=False)
