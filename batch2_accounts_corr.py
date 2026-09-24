import pandas as pd, numpy as np, combo_flex as C
F2="whale_sweep_output_futures2_{}/results.csv"
C.LEGS.update({"MGC#7068":(F2.format("MGC"),7068,10.0,1),"MCL#16931":(F2.format("MCL"),16931,100.0,1),"6J#9981":(F2.format("6J"),9981,12_500_000.0,10)})
A={"6E#5374":500,"6J#9981":375}; B={"MGC#7068":375,"MCL#16931":375,"MNQ#362":250}
T={l:C.leg_trades(l) for l in list(A)+list(B)}
first=max(min(t["t_in"] for t in v) for v in T.values()); last=min(max(t["t_in"] for t in v) for v in T.values())
T={l:[t for t in v if first<=t["t_in"]<=last] for l,v in T.items()}
def ev(cfg): return {d:C.walk_day(v,None) for d,v in C.size([t for l in cfg for t in T[l]],cfg).items()}
eA,eB=ev(A),ev(B)
dA=pd.Series({d:sum(p for _,p in v) for d,v in eA.items()}); dB=pd.Series({d:sum(p for _,p in v) for d,v in eB.items()})
print('daily pnl corr A vs B', round(pd.concat([dA,dB],axis=1).fillna(0).corr().iloc[0,1],3))
# pairwise leg correlation
S={l:pd.Series([t["r"] for t in v],index=[t["t_in"].date() for t in v]).groupby(level=0).sum() for l,v in T.items()}
print(pd.DataFrame(S).fillna(0).corr().round(2))
ra=[(s,C.run_eval(eA,days,None)) for s,days in C.cohorts(eA)]; rb=dict((s,C.run_eval(eB,days,None)) for s,days in C.cohorts(eB))
fa=sum(o=="FAIL" for s,(o,i) in ra); fb=sum(o=="FAIL" for o,i in rb.values()); both=sum(o=="FAIL" and rb.get(s,("",0))[0]=="FAIL" for s,(o,i) in ra)
print('cohorts',len(ra),'A fails',fa,'B fails',fb,'both',both)
