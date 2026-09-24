import os, pandas as pd, numpy as np, whale_sweep as ws, fundednext_flex_rules as fx
from screen_flex import params_from_row
pool=pd.read_csv('whale_sweep_output_futures/results.csv')
t=pd.read_csv('whale_sweep_output_futures/flex_screen_all.csv')
import sys
ids=[int(x) for x in sys.argv[1].split(',')]
out=[]
for pr in ids:
    row=pool.iloc[pr]; a=row.asset; tf=row.entry_timeframe
    df=ws.load_precomputed(a,tf); split=(df.index[0]+(df.index[-1]-df.index[0])*.6).date()
    variants=[('base',None,None)]
    if a=='6E': variants=[('6E cost, M6E x50',0.0000996,(12500,50)),('M6E cost, M6E x50',0.000228,(12500,50)),('6E cost, 6E x5',0.0000996,(125000,5))]
    elif a=='FDXM': variants=[('FDXM x5',None,(5.85,5))]
    else: variants=[('MNQ x50',None,(2.0,50))]
    for name,cost,(pv,mc) in variants:
        if cost is not None: ws.COST_TABLE['6E']=cost
        rec=ws.get_trade_records(df,params_from_row(row))
        R=np.array([r['r_multiple'] for r in rec]); pos=R[R>0]
        top1=np.sort(pos)[::-1][:max(1,len(R)//100)].sum()/R.sum() if R.sum()>0 else np.nan
        stops=np.array([r['risk'] for r in rec])
        for risk in (500,750,1000):
            for stop in (None,'1loss'):
                st=None if stop is None else min(risk,500)
                o=fx.simulate_flex(rec,df,risk,point_value=pv,max_contracts=mc,start_from=split,day_loss_stop=st)
                out.append(dict(row=pr,asset=a,tf=tf,tp=row.tp_mode,variant=name,PF=round(pos.sum()/-R[R<=0].sum(),2) if (R<=0).any() else 99,
                    maxR=round(R.max(),1),top1pct_share=round(top1,2),med_stop=round(float(np.median(stops)),5 if a=='6E' else 1),
                    risk=risk,stop=stop or '-',oos_pass=o.get('pass%'),oos_days=o.get('days_med'),oos_p90=o.get('days_p90'),capped=o.get('capped%')))
    ws.COST_TABLE['6E']=0.0000996
pd.set_option('display.width',260); d=pd.DataFrame(out)
d.to_csv(f'_deep_{ids[0]}.csv',index=False); print(d.to_string(index=False))
