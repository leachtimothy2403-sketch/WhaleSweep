#!/usr/bin/env python3
"""
Step 2 of the FundedNext Flex work (2026-09-24): bot-side overlays on a
shortlist of screened candidates -- risk per trade x daily profit cap x
daily loss stop. The best combo per candidate is chosen on IN-SAMPLE
cohorts only (first 60% of history, what the search saw); out-of-sample
(last 40%) is reported for that same combo.

Objective (IS): highest pass% among combos whose median days-to-pass is
within FLEX_MAX_DAYS (default 15); falls back to plain highest pass%.

    WS_HISTORY_YEARS=6 py -3 flex_overlays.py whale_sweep_output_mnq_6yr/results.csv 308,362,828,10,166,107,153
"""
import itertools
import os
import sys

import pandas as pd

import whale_sweep as ws
import fundednext_flex_rules as fx
from screen_flex import CONTRACTS, params_from_row

RISKS = [500, 750, 1000, 1250]
CAPS = [None, 1000, 1500, 2000]       # stop for the day once day P&L >= cap
STOPS = [None, 500, 1000, 1500]       # stop for the day once day P&L <= -stop
MAX_DAYS = float(os.environ.get("FLEX_MAX_DAYS", "15"))


def main(pool_path, rows):
    pool = pd.read_csv(pool_path)
    out = []
    for pr in rows:
        row = pool.iloc[pr]
        asset, tf = row["asset"], row["entry_timeframe"]
        df = ws.load_precomputed(asset, tf)
        split = (df.index[0] + (df.index[-1] - df.index[0]) * 0.6).date()
        rec = ws.get_trade_records(df, params_from_row(row))
        pv, mc = CONTRACTS[asset]
        grid = []
        for risk, cap, stop in itertools.product(RISKS, CAPS, STOPS):
            kw = dict(point_value=pv, max_contracts=mc, day_profit_cap=cap, day_loss_stop=stop)
            s = fx.simulate_flex(rec, df, risk, start_before=split, **kw)
            grid.append((risk, cap, stop, s))
        base = next(s for r, c, st, s in grid if r == 500 and c is None and st is None)
        ok = [g for g in grid if g[3]["days_med"] and g[3]["days_med"] <= MAX_DAYS]
        pick = max(ok or grid, key=lambda g: (g[3]["pass%"], -(g[3]["days_med"] or 999)))
        risk, cap, stop, iss = pick
        kw = dict(point_value=pv, max_contracts=mc, day_profit_cap=cap, day_loss_stop=stop)
        oos = fx.simulate_flex(rec, df, risk, start_from=split, **kw)
        oos_base = fx.simulate_flex(rec, df, 500, start_from=split, point_value=pv, max_contracts=mc)
        out.append({
            "cand": pr, "tf": tf, "tp_mode": row["tp_mode"],
            "base_is": f"{base['pass%']}% / {base['days_med']}d",
            "base_oos": f"{oos_base['pass%']}% / {oos_base['days_med']}d",
            "risk$": risk, "day_cap$": cap or "-", "day_stop$": stop or "-",
            "is_pass": iss["pass%"], "is_days": iss["days_med"],
            "oos_pass": oos["pass%"], "oos_fail": oos["fail%"], "oos_days": oos["days_med"],
            "oos_p90": oos["days_p90"], "oos_raised%": oos["target_raised%"],
        })
        # also keep the whole grid for this candidate
        pd.DataFrame([{"cand": pr, "risk": r, "cap": c, "stop": st, "is_pass": s["pass%"],
                       "is_days": s["days_med"], "is_p90": s["days_p90"]} for r, c, st, s in grid]
                     ).to_csv(f"flex_overlay_grid_{pr}.csv", index=False)
        print(f"cand {pr} done", flush=True)
    t = pd.DataFrame(out)
    pd.set_option("display.width", 250)
    print(t.to_string(index=False))
    t.to_csv("flex_overlays_summary.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1], [int(x) for x in sys.argv[2].split(",")])
