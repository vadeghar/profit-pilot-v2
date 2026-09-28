"""Recompute non-placeholder results from the Breeze-only Parquet cache."""
from __future__ import annotations
import json, glob, os
from collections import Counter
from datetime import datetime, timedelta, time
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'data'/'breeze_nifty_ratio_2025'
OUT=DATA/'computed_report.json'

def files_for(manifest,label_prefix):
    m=json.loads((DATA/'manifest.json').read_text())['requests']
    return [DATA/x['path'] for x in m.values() if x.get('label','').startswith(label_prefix) and 'path' in x]

def load_rows(paths):
    frames=[]
    for p in paths:
        try:
            d=pd.read_parquet(p)
            if not d.empty: frames.append(d)
        except Exception: pass
    if not frames: return pd.DataFrame()
    d=pd.concat(frames,ignore_index=True)
    d['ts']=pd.to_datetime(d['datetime'],errors='coerce')
    d=d.dropna(subset=['ts']).sort_values('ts').drop_duplicates('ts')
    d['close']=pd.to_numeric(d['close'],errors='coerce')
    return d.dropna(subset=['close'])

def entry_price(label):
    d=load_rows(files_for(None,label))
    if d.empty:return None
    exact=d[d.ts.astype(str).str.endswith('15:16:00')]
    if not exact.empty:return float(exact.iloc[0].close)
    after=d[d.ts.dt.strftime('%H:%M:%S')>'15:16:00']
    if not after.empty:return float(after.iloc[0].close)
    day=d[d.ts.dt.date==d.iloc[0].ts.date()]
    return float(day.iloc[-1].close) if not day.empty else None

def cost_rupees(premium_points,lot,slip):
    # Explicit conservative assumptions; not broker-confirmed.
    turnover=max(abs(premium_points)*lot,0.0)*4 + slip*lot*4
    brokerage=20*4
    exchange=turnover*.00035; sebi=turnover*.000001; stt=turnover*.001
    stamp=turnover*.00003; gst=.18*(brokerage+exchange)
    return brokerage+exchange+sebi+stt+stamp+gst

def run(hold_days=18, slip=0.0, margin_factor=1.0):
    base=json.loads((DATA/'backtest_results.json').read_text())
    out=[]
    for month,r in base.items():
        x=dict(r); x['computed']=True
        if r['decision'] in ('SKIP_DEBIT','SKIPPED_NO_HEDGE_PRICE','EXPIRY_UNCONFIRMED','MISSING_SPOT'):
            x.update(pnl_pct_margin=None,pnl_rupees=None,exit_reason=None); out.append(x); continue
        strikes=r.get('strikes') or {}
        if not all(k in strikes for k in ('near_buy','sell','hedge')):
            x['data_flags']=list(r.get('data_flags') or [])+['NO_COMPLETE_STRIKES']; out.append(x); continue
        entry=r['entry_date']; expiry=r['expiry']; lot=r['lots']; margin=float(r['margin'])*margin_factor
        prices={}
        for name,key in [('near','near_buy'),('sell','sell'),('hedge','hedge')]:
            prices[name]=entry_price(f'stage_a_{month}_{strikes[key]}')
        if any(v is None for v in prices.values()):
            x['data_flags']=list(r.get('data_flags') or [])+['MISSING_ENTRY_PRICE']; out.append(x); continue
        b={}
        for name,key in [('near','near_buy'),('sell','sell'),('hedge','hedge')]:
            d=load_rows(files_for(None,f'stage_b_{month}_{name}_{strikes[key]}'))
            if d.empty:b[name]=d;continue
            d=d.set_index('ts').sort_index(); b[name]=d['close'].resample('15min').last().dropna().reset_index()
        if any(getattr(v,'empty',True) for v in b.values()):
            x['data_flags']=list(r.get('data_flags') or [])+['MISSING_STAGE_B_LEG']; out.append(x); continue
        joined=b['near'].rename(columns={'close':'near'}).set_index('ts').join(b['sell'].rename(columns={'close':'sell'}).set_index('ts'),how='outer').join(b['hedge'].rename(columns={'close':'hedge'}).set_index('ts'),how='outer').sort_index()
        joined=joined.ffill(); joined=joined[joined.index>=pd.Timestamp(f'{entry} 15:16:00')]
        entry_debit=prices['near']-2*prices['sell']+prices['hedge']
        reason='MAX_HOLD'; exit_ts=joined.index[-1]; pnl_points=0.0
        deadline=pd.Timestamp(entry)+pd.Timedelta(days=hold_days)
        for ts,row in joined.iterrows():
            mtm=(row['near']-prices['near'])-2*(row['sell']-prices['sell'])+(row['hedge']-prices['hedge'])
            pct=(mtm*lot)/margin if margin else 0
            if pct>=.025: reason='TARGET';exit_ts=ts;pnl_points=mtm;break
            if pct<=-.03: reason='STOP_LOSS';exit_ts=ts;pnl_points=mtm;break
            if ts.date()>=deadline.date() and ts.time()>=time(15,15): reason='MAX_HOLD';exit_ts=ts;pnl_points=mtm;break
        else:
            last=joined.iloc[-1];pnl_points=(last['near']-prices['near'])-2*(last['sell']-prices['sell'])+(last['hedge']-prices['hedge']); exit_ts=joined.index[-1]
        gross=pnl_points*lot; costs=cost_rupees(entry_debit,lot,slip); net=gross-costs
        x.update(entry_prices=prices,entry_debit_points=entry_debit,exit_time=str(exit_ts),exit_reason=reason,pnl_pct_margin=net/margin,pnl_rupees=net,costs_rupees=costs,hold_days=(exit_ts-pd.Timestamp(f'{entry} 15:16:00')).total_seconds()/86400,data_flags=list(r.get('data_flags') or [])+(['FORWARD_FILLED'] if joined.isna().any().any() else []))
        out.append(x)
    trades=[x for x in out if x.get('pnl_rupees') is not None]
    wins=[x for x in trades if x['pnl_rupees']>0]; losses=[x for x in trades if x['pnl_rupees']<=0]
    grosswin=sum(x['pnl_rupees'] for x in wins); grossloss=-sum(x['pnl_rupees'] for x in losses)
    equity=[]; value=0; peak=0; dd=0
    for x in out:
        if x.get('pnl_rupees') is not None:value+=x['pnl_rupees']
        peak=max(peak,value);dd=max(dd,peak-value);equity.append({'month':x['month'],'pnl':value,'drawdown':peak-value})
    return {'hold_days':hold_days,'slippage':slip,'margin_factor':margin_factor,'months':out,'summary':{'trades':len(trades),'wins':len(wins),'win_rate':len(wins)/len(trades) if trades else None,'expectancy':sum(x['pnl_rupees'] for x in trades)/len(trades) if trades else None,'profit_factor':grosswin/grossloss if grossloss else None,'total_pnl':value,'max_drawdown':dd,'exit_reasons':dict(Counter(x['exit_reason'] for x in trades)),'avg_hold_days':sum(x['hold_days'] for x in trades)/len(trades) if trades else None},'equity':equity}

def main():
    reports={f'h{h}_s{s}_m{m}':run(h,s,m) for h in (18,19) for s in (0,1,2) for m in (0.6,0.8,1,1.2,1.4)}
    OUT.write_text(json.dumps(reports,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:v['summary'] for k,v in reports.items() if k in ('h18_s0_m1','h19_s0_m1','h18_s1_m1','h18_s2_m1')},sort_keys=True))
if __name__=='__main__':main()