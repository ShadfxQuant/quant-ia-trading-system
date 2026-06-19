"""
Buy-&-hold ALL assets vs the SYSTEM over all assets (Part 8.44).

Two honest views, both from $100k:
  A. Buy-&-hold basket  — $25k held in each of SPY/^NDX/GLD/GC=F (equal weight).
  B. System (pooled)    — $100k run through the gated engine across all 4
                          symbols as one shared-capital book (how it actually
                          runs), 10bp friction.
Plus per-asset buy-hold vs system totals. Common date window for fairness.
"""
from __future__ import annotations
import warnings, logging, json
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np, pandas as pd
from config.settings import TRENDCARRY, get_pullback_cfg
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

INITIAL = 100_000.0
SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
GATE = {"SPY":(True,False),"^NDX":(True,False),"GLD":(True,False),"GC=F":(False,True)}


def gate(df, col, rsi, hmm):
    sig=df[col].copy(); r=df.get("RSI_14"); pb=df.get("P_bull"); pbr=df.get("P_bear")
    blk=pd.Series(False,index=df.index)
    if rsi and r is not None: blk|=(sig==-1)&(r<40); blk|=(sig==1)&(r>60)
    if hmm and pb is not None and pbr is not None: blk|=(sig==1)&(pbr>0.6); blk|=(sig==-1)&(pb>0.6)
    sig[blk]=0; return sig


def metrics(eq):
    eq=eq.dropna(); total=eq.iloc[-1]/eq.iloc[0]-1
    yrs=max((eq.index[-1]-eq.index[0]).days/365.25,0.1)
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=float(((eq-eq.cummax())/eq.cummax()).min())
    return {"total":round(total*100,1),"cagr":round(cagr*100,1),"dd":round(dd*100,1)}


def main():
    print("="*92); print("  BUY-&-HOLD ALL ASSETS vs SYSTEM OVER ALL ASSETS (Part 8.44)"); print("="*92)

    prices={}; all_trades=[]; per_asset={}
    for s in SYMBOLS:
        raw=load_symbol(s); df=prepare_dual(raw)
        if df.index.tz: df.index=df.index.tz_localize(None)
        prices[s]=raw["Close"].resample("1D").last()
        if prices[s].index.tz: prices[s].index=prices[s].index.tz_localize(None)
        df["pullback_Signal"]=gate(df,"pullback_Signal",*GATE[s])
        df["trend_carry_Signal"]=gate(df,"trend_carry_Signal",*GATE[s])
        cfg=get_pullback_cfg(s)
        tr=run_portfolio(df,[StrategySpec("pullback",cfg,pb_exit(cfg)),
                             StrategySpec("trend_carry",TRENDCARRY,tc_exit())],
                         symbol=s,initial_capital=INITIAL)["trades"].copy()
        tr["symbol"]=s; all_trades.append(tr)
        per_asset[s]=tr

    P=pd.DataFrame(prices).dropna()           # common window, all 4 present
    idx=P.index
    t0,t1=idx[0],idx[-1]
    print(f"\n  common window: {t0.date()} → {t1.date()}\n")

    # A. equal-weight buy-hold basket ($25k each)
    basket=sum((INITIAL/4)*(P[s]/P[s].iloc[0]) for s in SYMBOLS)

    # B. system pooled ($100k shared, 10bp friction), restricted to common window
    combined=pd.concat(all_trades,ignore_index=True)
    combined["exit_time"]=pd.to_datetime(combined["exit_time"]).dt.tz_localize(None)
    combined=combined[(combined["exit_time"]>=t0)&(combined["exit_time"]<=t1)].sort_values("exit_time")
    eq=INITIAL; eqs=[]; fric=(10/10000.0)*0.35
    for _,tr in combined.iterrows():
        r=tr["pnl"]/eq - fric; eq*=(1+r); eqs.append(eq)
    sys_eq=pd.Series(eqs,index=combined["exit_time"].values)
    sys_eq=sys_eq.groupby(level=0).last()           # dedup same-day exits -> last equity
    sys_daily=sys_eq.reindex(idx,method="ffill").fillna(INITIAL)

    mb=metrics(basket); ms=metrics(sys_daily)
    print("  ── COMBINED ($100k each basis) ──")
    print(f"  {'':<26}{'TotalRet':>10}{'CAGR':>9}{'MaxDD':>9}")
    print("  "+"-"*54)
    print(f"  {'Buy & hold all 4 (eq-wt)':<26}{mb['total']:>+9.1f}%{mb['cagr']:>+8.1f}%{mb['dd']:>+8.1f}%")
    print(f"  {'System over all 4 (pooled)':<26}{ms['total']:>+9.1f}%{ms['cagr']:>+8.1f}%{ms['dd']:>+8.1f}%")

    # per-asset
    print("\n  ── PER ASSET (buy-hold vs system, common window) ──")
    print(f"  {'asset':<8}{'B&H total':>11}{'B&H DD':>9}{'sys total':>11}{'sys DD':>9}")
    print("  "+"-"*48)
    pa={}
    for s in SYMBOLS:
        bh=INITIAL*(P[s]/P[s].iloc[0]); mbh=metrics(bh)
        tr=per_asset[s].copy(); tr["exit_time"]=pd.to_datetime(tr["exit_time"]).dt.tz_localize(None)
        tr=tr[(tr["exit_time"]>=t0)&(tr["exit_time"]<=t1)].sort_values("exit_time")
        e=INITIAL; ee=[]
        for _,t in tr.iterrows():
            r=t["pnl"]/e - fric; e*=(1+r); ee.append(e)
        se=pd.Series(ee,index=tr["exit_time"].values).groupby(level=0).last()
        sd=se.reindex(idx,method="ffill").fillna(INITIAL); msd=metrics(sd)
        pa[s]={"bh":mbh,"sys":msd}
        print(f"  {s:<8}{mbh['total']:>+10.1f}%{mbh['dd']:>+8.1f}%{msd['total']:>+10.1f}%{msd['dd']:>+8.1f}%")

    def ds(s,n=90):
        s=s.dropna()
        if len(s)<=n: return [round(x) for x in s.tolist()]
        st=len(s)/n; return [round(s.iloc[min(int(i*st),len(s)-1)]) for i in range(n)]
    out={"window":[str(t0.date()),str(t1.date())],"basket":ds(basket),"system":ds(sys_daily),
         "mb":mb,"ms":ms,"per_asset":pa,
         "per_asset_curves":{s:{"bh":ds(INITIAL*(P[s]/P[s].iloc[0]))} for s in SYMBOLS}}
    json.dump(out,open("research/results/all_assets.json","w"),indent=2,default=str)
    print("\n  wrote → research/results/all_assets.json")


if __name__ == "__main__":
    main()
