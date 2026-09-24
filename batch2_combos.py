"""Batch-2 (MGC/MCL/6J) candidates alone and combined with 6E #5374 / #12654 / MNQ #362
in one FundedNext Flex account, event-accurate (combo_flex engine). Usage: py -3 batch2_combos.py <part 0|1>"""
import sys, pandas as pd, combo_flex as C
F2 = "whale_sweep_output_futures2_{}/results.csv"
C.LEGS.update({
    "6E#12654": ("whale_sweep_output_futures/results.csv", 12654, 125_000.0, 10),
    "MGC#7068": (F2.format("MGC"), 7068, 10.0, 1), "MGC#4104": (F2.format("MGC"), 4104, 10.0, 1),
    "MCL#8044": (F2.format("MCL"), 8044, 100.0, 1), "MCL#16931": (F2.format("MCL"), 16931, 100.0, 1),
    "6J#9981": (F2.format("6J"), 9981, 12_500_000.0, 10),
})
CFGS = [
    {"MGC#7068": 500}, {"MGC#4104": 500}, {"MCL#8044": 500}, {"MCL#16931": 500}, {"6J#9981": 500},
    {"6E#5374": 500, "MGC#7068": 375}, {"6E#5374": 500, "MCL#16931": 375}, {"6E#5374": 500, "MGC#7068": 250, "MCL#16931": 250},
    {"MGC#7068": 375, "MCL#16931": 375}, {"MGC#7068": 375, "MCL#16931": 375, "MNQ#362": 250},
    {"6E#12654": 500, "MNQ#362": 250, "MGC#7068": 375}, {"6E#5374": 500, "6J#9981": 375},
]
if __name__ != "__main__": raise SystemExit  # imported only for LEGS
part = int(sys.argv[1]); cfgs = CFGS[part::2]
legs = sorted({l for c in cfgs for l in c})
T = {l: C.leg_trades(l) for l in legs}
# common window across ALL legs used anywhere so every config is comparable
first = max(min(t["t_in"] for t in T[l]) for l in legs); last = min(max(t["t_in"] for t in T[l]) for l in legs)
first = max(first, pd.Timestamp("2020-09-22", tz="UTC")); last = min(last, pd.Timestamp("2026-09-15", tz="UTC"))
split = (first + (last - first) * 0.6).date()
rows = []
for cfg in cfgs:
    tr = [t for l in cfg for t in T[l] if first <= t["t_in"] <= last]
    r = C.evaluate(tr, cfg, None, split)
    rows.append({"legs": " + ".join(f"{k} ${v}" for k, v in cfg.items()), **r}); print(rows[-1], flush=True)
pd.DataFrame(rows).to_csv(f"batch2_combos_{part}.csv", index=False)
