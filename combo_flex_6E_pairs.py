import itertools, pandas as pd, combo_flex as C
for r in (12654,41779,15379):
    C.LEGS[f"6E#{r}"]=("whale_sweep_output_futures/results.csv", r, 125_000.0, 10)
legs=["6E#5374","6E#12654","6E#41779","6E#15379","MNQ#362"]
T={l:C.leg_trades(l) for l in legs}
first=max(min(t["t_in"] for t in T[l]) for l in legs); last=min(max(t["t_in"] for t in T[l]) for l in legs)
split=(first+(last-first)*0.6).date()
rows=[]
cfgs=[{"6E#5374":500},{"6E#12654":500},{"6E#41779":500},{"6E#15379":500},
      {"6E#5374":375,"6E#12654":375},{"6E#5374":250,"6E#12654":250},{"6E#5374":375,"6E#41779":375},{"6E#5374":375,"6E#15379":375},
      {"6E#5374":500,"MNQ#362":250},{"6E#5374":375,"6E#12654":250,"MNQ#362":250}]
for cfg in cfgs:
    tr=[t for l in cfg for t in T[l] if first<=t["t_in"]<=last]
    for stop in (None,1000):
        r=C.evaluate(tr,cfg,stop,split)
        rows.append({"legs":" + ".join(f"{k} ${v}" for k,v in cfg.items()),"stop":stop or "-",**r}); print(rows[-1],flush=True)
pd.DataFrame(rows).to_csv("combo_flex_6E_pairs.csv",index=False)
