import sys, pickle, pandas as pd, whale_sweep as ws
from screen_flex import params_from_row
a=sys.argv[1]; d=f'whale_sweep_output_futures2_{a}'
pool=pd.read_csv(f'{d}/results.csv'); t=pd.read_csv(f'{d}/flex_screen_all.csv')
rows=[]
for _,r in t.iterrows():
    best=None
    for k in (500,750,1000,1250,1500):
        p,dd=r.get(f'is_pass_{k}'),r.get(f'is_days_{k}')
        if pd.notna(p) and p>=88 and dd<=18:
            key=(p,-dd)
            if best is None or key>best[0]: best=(key,k)
    if best: rows.append((r.pool_row,best[0][0],-best[0][1]))
s=sorted(rows,key=lambda x:(-x[1],x[2]))[:25]
print(a,'shortlist',len(rows),'-> keep',len(s))
out={}; dfs={}
for pr,_,_ in s:
    row=pool.iloc[pr]; tf=row.entry_timeframe
    if tf not in dfs: dfs[tf]=ws.load_precomputed(a,tf)
    df=dfs[tf]; rec=ws.get_trade_records(df,params_from_row(row))
    out[pr]=[(df.index[x['entry_idx']],x['r_multiple']) for x in rec]
pickle.dump(out,open(f'_b2_{a}.pkl','wb')); print('done',len(out))
