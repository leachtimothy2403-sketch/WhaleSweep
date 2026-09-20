import json, sys, heapq
from collections import defaultdict
sys.path.insert(0, '.')
import whale_sweep as ws
import gate2_holdout as g2
import ftmo_challenge_rules as ftmo

START_EQUITY = ftmo.START_EQUITY
LEVERAGE = {'EURUSD': 100, 'GBPUSD': 100, 'GER40': 50, 'NDX100': 50, 'SPX500': 50, 'US30': 50, 'XAUUSD': 30}

with open('whale_sweep_output/top_strategies.json') as f:
    top = json.load(f)

def rows_for(asset, tf):
    return [r for r in top if r['asset'] == asset and r['entry_timeframe'] == tf]

def load_leg(asset, tf, local_rank, risk_pct):
    rows = rows_for(asset, tf)
    row = rows[local_rank]
    df = ws.load_precomputed(asset, tf)
    p = g2._candidate_params(row)
    records = ws.get_trade_records(df, p)
    lev = LEVERAGE[asset]
    risk_amt = START_EQUITY * (risk_pct / 100.0)
    trades = []
    for r in records:
        t_in = df.index[r['entry_idx']]
        t_out = df.index[r['exit_idx']]
        stop_dist = r['risk']
        entry_px = r['entry']
        if stop_dist <= 0:
            continue
        position_size = risk_amt / stop_dist
        notional = position_size * entry_px
        margin = notional / lev
        dollar_pnl = risk_amt * float(r['r_multiple'])
        trades.append({'entry': t_in, 'exit': t_out, 'pnl': dollar_pnl, 'margin': margin, 'leg': f"{asset}/{tf}#{local_rank}"})
    return trades

def margin_gate(all_trades, capacity):
    all_trades = sorted(all_trades, key=lambda t: (t['entry'], t['leg']))
    heap = []
    used = 0.0
    kept = []
    for t in all_trades:
        while heap and heap[0][0] <= t['entry']:
            _, m = heapq.heappop(heap)
            used -= m
        if used + t['margin'] <= capacity:
            used += t['margin']
            heapq.heappush(heap, (t['exit'], t['margin']))
            kept.append(t)
    return kept

def days_to_pass_stats(trades, label):
    trades_sorted = sorted(trades, key=lambda t: t['entry'])
    by_date = defaultdict(list)
    for t in trades_sorted:
        by_date[t['entry'].date()].append(t['pnl'])
    all_days = sorted(by_date.keys())
    if len(all_days) < 8:
        print(f"{label}: not enough days")
        return
    mondays = ftmo.get_mondays_full(all_days)
    outcomes = [ftmo.simulate_2step_portfolio(dict(by_date), all_days, start) for start in mondays]
    n = len(outcomes)
    pass_days = sorted(o['calendar_days'] for o in outcomes if o['outcome'] == 'PASS' and o['calendar_days'])
    n_pass = len(pass_days)
    n_fail = sum(1 for o in outcomes if o['outcome'] == 'FAIL')
    pass_pct = 100.0*n_pass/n if n else None
    if pass_days:
        import statistics
        median = statistics.median(pass_days)
        p10 = pass_days[int(0.10*len(pass_days))]
        p90 = pass_days[min(int(0.90*len(pass_days)), len(pass_days)-1)]
        mn, mx = pass_days[0], pass_days[-1]
        print(f"{label}: n_cohorts={n} pass_pct={pass_pct:.1f} n_pass={n_pass} "
              f"days_to_pass[min={mn} p10={p10} median={median} p90={p90} max={mx}]")
    else:
        print(f"{label}: n_cohorts={n} pass_pct={pass_pct} n_pass=0 (no passes to measure days on)")

def analyze(name, legs):
    all_trades = []
    for asset, tf, rank, risk in legs:
        all_trades.extend(load_leg(asset, tf, rank, risk))
    print(f"=== {name} ({len(legs)} legs, {len(all_trades)} raw trades) ===")
    days_to_pass_stats(all_trades, f"{name} BASELINE (no gate)")
    for cap_frac in [1.0, 0.8, 0.5]:
        cap = START_EQUITY * cap_frac
        gated = margin_gate(all_trades, cap)
        days_to_pass_stats(gated, f"{name} GATED (cap={cap_frac*100:.0f}% eq)")

NEW_COMBO = [
    ('GER40', '5min', 0, 2.122),
    ('EURUSD', '3min', 2, 0.613),
    ('XAUUSD', '3min', 3, 1.365),
    ('NDX100', '5min', 0, 2.027),
]
OLD_COMBO = [
    ('GER40', '3min', 4, 1.106),
    ('EURUSD', '5min', 6, 0.962),
    ('XAUUSD', '5min', 5, 1.518),
]

analyze("NEW combo", NEW_COMBO)
print()
analyze("OLD combo", OLD_COMBO)
