"""Quantify the session_open_930 look-ahead: same candidates with WS_CAUSAL_OPEN off vs on."""
import os, sys, importlib
import pandas as pd
rows=[]
for flag in ("1",):
    os.environ["WS_CAUSAL_OPEN"]=flag
    import whale_sweep as ws; importlib.reload(ws)
    import fundednext_flex_rules as fx
    from screen_flex import params_from_row, CONTRACTS
    for path,ri in [("whale_sweep_output_futures/results.csv",5374),("whale_sweep_output_futures/results.csv",12654),("whale_sweep_output_mnq_6yr/results.csv",362),
                    ("whale_sweep_output_futures2_MGC/results.csv",7068),("whale_sweep_output_futures2_MCL/results.csv",8044),("whale_sweep_output_futures2_6J/results.csv",9981)]:
        row=pd.read_csv(path).iloc[ri]; a=row.asset; df=ws.load_precomputed(a,row.entry_timeframe)
        rec=ws.get_trade_records(df,params_from_row(row)); sm=ws.summarize_trades(rec)
        split=(df.index[0]+(df.index[-1]-df.index[0])*0.6).date(); pv,mc=CONTRACTS[a]
        o=fx.simulate_flex(rec,df,500,point_value=pv,max_contracts=mc,start_from=split)
        rows.append(dict(causal=flag,leg=f"{a}#{ri}",tp=row.tp_mode,sess_start=int(row.session_start_minutes),trades=sm["n_trades"],win=sm["win_rate_pct"],PF=sm["profit_factor"],expR=sm["expectancy_r"],oos_pass=o.get("pass%"),oos_days=o.get("days_med")))
        print(rows[-1],flush=True)
pd.DataFrame(rows).to_csv("lookahead_check_causal_london.csv",index=False)
