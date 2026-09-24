import pickle, pandas as pd, whale_sweep as ws
from screen_flex import params_from_row
ref={}
for name,path,row_i,asset in [("5374","whale_sweep_output_futures/results.csv",5374,"6E"),("12654","whale_sweep_output_futures/results.csv",12654,"6E"),("MNQ362","whale_sweep_output_mnq_6yr/results.csv",362,"MNQ")]:
    row=pd.read_csv(path).iloc[row_i]; df=ws.load_precomputed(asset,row.entry_timeframe); rec=ws.get_trade_records(df,params_from_row(row))
    ref[name]=pd.Series([x['r_multiple'] for x in rec],index=[df.index[x['entry_idx']].date() for x in rec]).groupby(level=0).sum()
rows=[]
for a in ['MGC','MCL','6J']:
    tr=pickle.load(open(f'_b2_{a}.pkl','rb')); d=f'whale_sweep_output_futures2_{a}'
    pool=pd.read_csv(f'{d}/results.csv'); t=pd.read_csv(f'{d}/flex_screen_all.csv').set_index('pool_row')
    for pr,v in tr.items():
        s=pd.Series([x[1] for x in v],index=[x[0].date() for x in v]).groupby(level=0).sum()
        D=pd.concat([s]+list(ref.values()),axis=1).fillna(0); c=D.corr().iloc[0]
        row=pool.iloc[pr]; r=t.loc[pr]
        rows.append(dict(asset=a,row=pr,tf=row.entry_timeframe,tp=row.tp_mode,conf=row.confirmation_mode,rr=row.rr,
            sess=f"{row.session_start_minutes//60:02d}:{row.session_start_minutes%60:02d}-{row.session_end_minutes//60:02d}:{row.session_end_minutes%60:02d}",
            pf=round(row.profit_factor,2),win=round(row.win_rate_pct,1),trades_wk=round(len(v)/313,1),
            c5374=round(c.iloc[1],2),c12654=round(c.iloc[2],2),cMNQ=round(c.iloc[3],2),
            is_pass=r.is_pass,oos_pass=r.oos_pass,oos_days=r.oos_days_med,risk=r.best_risk,capped=r['capped%']))
x=pd.DataFrame(rows); x.to_csv('batch2_shortlist_corr.csv',index=False)
pd.set_option('display.width',260)
for a in ['MGC','MCL','6J']:
    print(f'== {a}'); print(x[x.asset==a].sort_values(['oos_pass','oos_days'],ascending=[False,True]).head(10).drop(columns='asset').to_string(index=False))
