"""
Infinex scenario — fractional sizing, 0.1% fee + funding, leverage cliff (Part 8.49).

Infinex (perp DEX) vs MT5:
  GOOD:  no minimum lot sizes -> fractional sizing works on small accounts;
         flat % fees scale -> the small-account friction problem goes away.
  COST:  0.1% trade fee + FUNDING over multi-day holds (our avg hold ~days).
         Effective friction is higher than the 10bp MC assumed.
  DANGER: 'crazy leverage' -> our -11% to -16% drawdowns become LIQUIDATIONS.

Models leverage WITH liquidation: per trade eq *= (1 + L*r); if (1+L*r) <= 0
the position liquidates the account (eq -> 0). Block bootstrap preserves
loss clusters. Reports $700-start final value + P(ruin) up the leverage curve,
across friction tiers (10 / 30 / 60 bp) to bracket fee+funding.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np, pandas as pd
from config.settings import TRENDCARRY, get_pullback_cfg
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

START=700.0; SYMBOLS=["SPY","^NDX","GLD","GC=F"]
GATE={"SPY":(True,False),"^NDX":(True,False),"GLD":(True,False),"GC=F":(False,True)}
RNG=np.random.default_rng(2026); N=10_000; BLOCK=10


def gate(df,col,rsi,hmm):
    sig=df[col].copy(); r=df.get("RSI_14"); pb=df.get("P_bull"); pbr=df.get("P_bear")
    blk=pd.Series(False,index=df.index)
    if rsi and r is not None: blk|=(sig==-1)&(r<40); blk|=(sig==1)&(r>60)
    if hmm and pb is not None and pbr is not None: blk|=(sig==1)&(pbr>0.6); blk|=(sig==-1)&(pb>0.6)
    sig[blk]=0; return sig

def prep(s):
    df=prepare_dual(load_symbol(s))
    if df.index.tz: df.index=df.index.tz_localize(None)
    r,h=GATE[s]; df["pullback_Signal"]=gate(df,"pullback_Signal",r,h); df["trend_carry_Signal"]=gate(df,"trend_carry_Signal",r,h)
    return df

def base_tr(s,df):
    cfg=get_pullback_cfg(s)
    tr=run_portfolio(df,[StrategySpec("pullback",cfg,pb_exit(cfg)),StrategySpec("trend_carry",TRENDCARRY,tc_exit())],
                     symbol=s,initial_capital=100000.0)["trades"]
    return [(pd.to_datetime(t).tz_localize(None), p/100000.0) for t,p in zip(tr["exit_time"],tr["pnl"])]

def sim_copy(dg,t,side):
    sub=dg[dg.index>=t]
    if len(sub)<2: return None,None
    ep=sub["Close"].iloc[0]
    for i in range(1,min(len(sub),585)):
        hi=sub["High"].iloc[i];lo=sub["Low"].iloc[i]
        if side>0:
            if lo<=ep*0.9625: return -0.0375,sub.index[i]
            if hi>=ep*1.075: return 0.075,sub.index[i]
        else:
            if hi>=ep*1.0375: return -0.0375,sub.index[i]
            if lo<=ep*0.925: return 0.075,sub.index[i]
    j=min(len(sub)-1,584); xp=sub["Close"].iloc[j]
    return (((xp/ep-1) if side>0 else (ep/xp-1))), sub.index[j]

def copies(dfs):
    out=[]
    for ld,lg in [("SPY","^NDX"),("^NDX","SPY")]:
        dl,dg=dfs[ld],dfs[lg]; sig=dl["pullback_Signal"]
        r20l=dl["Close"].pct_change(20); r20g=dg["Close"].pct_change(20)
        pb=dl.get("P_bull"); pbr=dl.get("P_bear")
        for t in dl.index[sig!=0]:
            s=int(sig.loc[t]); conv=(pb.loc[t] if s>0 else pbr.loc[t])
            if conv!=conv or conv<0.65: continue
            if t not in r20g.index or not (r20g.loc[t]*s<r20l.loc[t]*s): continue
            mv,xt=sim_copy(dg,t,s)
            if mv is None: continue
            out.append((xt, mv*0.15))   # 15% size return (pre-friction)
    return out

def mc(rets, lev, fric, years=3):
    """Block bootstrap with leverage + liquidation. rets are pre-friction per-trade returns (frac of equity)."""
    r=np.array([x-fric for x in rets])      # subtract friction per trade
    n=len(r); tpy=n/YEARS_REALIZED; nt=max(BLOCK,int(round(tpy*years))); nb=int(np.ceil(nt/BLOCK))
    starts=RNG.integers(0,max(1,n-BLOCK),size=(N,nb))
    finals=np.empty(N); liq=np.zeros(N,dtype=bool)
    LIQ_LEVEL=0.10        # margin call / liquidation if equity drops to 10% of start (intra-path)
    for p in range(N):
        seq=np.concatenate([r[s:s+BLOCK] for s in starts[p]])[:nt]
        eq=1.0; dead=False
        for x in seq:
            g=1+lev*x
            if g<=0: eq=0.0; dead=True; break          # single-trade wipeout
            eq*=g
            if eq<=LIQ_LEVEL: eq=0.0; dead=True; break  # intra-path margin liquidation
        finals[p]=eq*START; liq[p]=dead
    return {"p50":np.quantile(finals,.5),"p5":np.quantile(finals,.05),
            "p_ruin":(finals<0.5*START).mean(),"p_liq":liq.mean()}

def main():
    global YEARS_REALIZED
    print("="*100); print("  INFINEX SCENARIO — $700 start, fractional sizing, fee+funding, leverage cliff (Part 8.49)"); print("="*100)
    dfs={s:prep(s) for s in SYMBOLS}
    allt=[]
    for s in SYMBOLS: allt+=base_tr(s,dfs[s])
    allt+=copies(dfs)
    allt.sort(key=lambda x:x[0])
    rets=[r for _,r in allt]
    days=(allt[-1][0]-allt[0][0]).days; YEARS_REALIZED=max(days/365.25,0.1)
    print(f"\n  {len(rets)} trades over {YEARS_REALIZED:.1f}y  ·  start ${START:.0f}\n")

    # friction tiers: 0.1% round-trip ~10bp of notional;  +funding -> 30/60bp effective
    # per-trade friction on equity = bps/10000 * avg_notional_frac(~0.28)
    fr = {"10bp (fee only, optimistic)":0.0010*0.28,
          "30bp (fee+light funding)":0.0030*0.28,
          "60bp (fee+heavy funding)":0.0060*0.28}

    for fname,fric in fr.items():
        print(f"  ── friction: {fname} ──")
        print(f"  {'leverage':<10}{'p50 $':>12}{'p5 $':>12}{'P(ruin)':>10}{'P(liquidated)':>15}")
        print("  "+"-"*59)
        for lev in [1,1.5,2,3,5,10,25]:
            m=mc(rets,lev,fric)
            flag=""
            if m["p_liq"]>0.01: flag=" ⚠"
            if m["p_liq"]>0.5: flag=" ☠ blowup"
            print(f"  {lev:<10}{m['p50']:>+12,.0f}{m['p5']:>+12,.0f}{m['p_ruin']*100:>9.1f}%{m['p_liq']*100:>14.1f}%{flag}")
        print()


if __name__ == "__main__":
    main()
