"""Three FundedNext Flex accounts (2026-09-24), event-accurate (combo_flex engine).
    WS_HISTORY_YEARS=6 py -3 portfolios3.py"""
import itertools, json
import numpy as np, pandas as pd
import combo_flex as C
import portfolios3_legs  # noqa: registers legs

P = {
    "P1": {"6E#5374": 500, "MCL#8044": 250, "MGC#4104": 250},
    "P2": {"6J#9981": 500, "MGC#7068": 375, "MNQ#362": 250},
    "P3": {"6E#12654": 500, "MCL#11854": 250, "MGC#9163": 250},
}
legs = sorted({l for p in P.values() for l in p})
T = {l: C.leg_trades(l) for l in legs}
first = max(max(min(t["t_in"] for t in T[l]) for l in legs), pd.Timestamp("2020-09-22", tz="UTC"))
last = min(min(max(t["t_in"] for t in T[l]) for l in legs), pd.Timestamp("2026-09-15", tz="UTC"))
T = {l: [t for t in v if first <= t["t_in"] <= last] for l, v in T.items()}
split = (first + (last - first) * 0.6).date()

def events(cfg):
    return {d: C.walk_day(v, None) for d, v in C.size([t for l in cfg for t in T[l]], cfg).items()}

rows, EV, OUT = [], {}, {}
for name, cfg in P.items():
    r = C.evaluate([t for l in cfg for t in T[l]], cfg, None, split)
    r.pop("12m E$ uncapped", None)
    rows.append({"portfolio": name, "legs": " + ".join(f"{k} ${v}" for k, v in cfg.items()), **r})
    EV[name] = events(cfg)
    OUT[name] = {s: C.run_eval(EV[name], days, None)[0] for s, days in C.cohorts(EV[name])}
t = pd.DataFrame(rows)
pd.set_option("display.width", 260); print(t.to_string(index=False))
daily = pd.DataFrame({n: pd.Series({d: sum(p for _, p in v) for d, v in e.items()}) for n, e in EV.items()}).fillna(0)
print("\ndaily P&L correlation between accounts:\n", daily.corr().round(3))
starts = sorted(set.intersection(*[set(o) for o in OUT.values()]))
fails = {n: sum(OUT[n][s] == "FAIL" for s in starts) for n in P}
pair = {f"{a}&{b}": sum(OUT[a][s] == "FAIL" and OUT[b][s] == "FAIL" for s in starts) for a, b in itertools.combinations(P, 2)}
allf = sum(all(OUT[n][s] == "FAIL" for n in P) for s in starts)
anyp = sum(any(OUT[n][s] == "PASS" for n in P) for s in starts)
print(f"\n{len(starts)} weekly starts: fails {fails}; joint {pair}; all three {allf}; at least one passes {anyp}")
t.to_csv("portfolios3.csv", index=False)
json.dump({"split": str(split), "window": [str(first.date()), str(last.date())], "fails": fails, "joint": pair,
           "all3": allf, "n": len(starts), "corr": daily.corr().round(3).to_dict()}, open("portfolios3.json", "w"))
