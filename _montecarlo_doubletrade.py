"""
Monte Carlo on the system WITH the safely-sized double-trade overlay (Part 8.48).

Safety constraints applied (from Part 8.47):
  - copy size = 15% notional (HALF the base 30%)         ← conservative
  - conviction > 0.65 (HMM posterior aligned)            ← only confirmed-strong
  - laggard confirmation (other index trailing)          ← lead-lag
  - 10bp friction on copies
  - tested at MODEST leverage only (1.0 / 1.25 / 1.5×)   ← don't stack high lev

Methodology: BLOCK bootstrap (blocks of 10 consecutive trades) so the
correlated-loss clusters that cause the copy book's deep drawdown are
PRESERVED — a standard IID bootstrap would hide that risk.

Compares base-only vs base+overlay: CAGR, p5 DD, P(ruin), P(2×).
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

INITIAL=100_000.0; SYMBOLS=["SPY","^NDX","GLD","GC=F"]
GATE={"SPY":(True,False),"^NDX":(True,False),"GLD":(True,False),"GC=F":(False,True)}
COPY_SIZE=0.15; FRIC=(10/10000.0)*COPY_SIZE
RNG=np.random.default_rng(2026); N_PATHS=10_000; BLOCK=10


def gate(df,col,rsi,hmm):
    sig=df[col].copy(); r=df.get("RSI_14"); pb=df.get("P_bull"); pbr=df.get("P_bear")
    blk=pd.Series(False,index=df.index)
    if rsi and r is not None: blk|=(sig==-1)&(r<40); blk|=(sig==1)&(r>60)
    if hmm and pb is not None and pbr is not None: blk|=(sig==1)&(pbr>0.6); blk|=(sig==-1)&(pb>0.6)
    sig[blk]=0; return sig


def prep(sym):
    df=prepare_dual(load_symbol(sym))
    if df.index.tz: df.index=df.index.tz_localize(None)
    r,h=GATE[sym]
    df["pullback_Signal"]=gate(df,"pullback_Signal",r,h)
    df["trend_carry_Signal"]=gate(df,"trend_carry_Signal",r,h)
    return df


def base_trades(sym,df):
    cfg=get_pullback_cfg(sym)
    tr=run_portfolio(df,[StrategySpec("pullback",cfg,pb_exit(cfg)),
                         StrategySpec("trend_carry",TRENDCARRY,tc_exit())],
                     symbol=sym,initial_capital=INITIAL)["trades"].copy()
    tr["et"]=pd.to_datetime(tr["exit_time"]).dt.tz_localize(None)
    return tr[["et","pnl"]]


def sim_copy(dfg,t,side,stop,tp,maxb):
    sub=dfg[dfg.index>=t]
    if len(sub)<2: return None,None
    ep=sub["Close"].iloc[0]
    for i in range(1,min(len(sub),maxb)):
        hi=sub["High"].iloc[i]; lo=sub["Low"].iloc[i]; xt=sub.index[i]
        if side>0:
            if lo<=ep*(1-stop): return -stop-FRIC,xt
            if hi>=ep*(1+tp): return tp-FRIC,xt
        else:
            if hi>=ep*(1+stop): return -stop-FRIC,xt
            if lo<=ep*(1-tp): return tp-FRIC,xt
    j=min(len(sub)-1,maxb-1); xp=sub["Close"].iloc[j]
    return (((xp/ep-1) if side>0 else (ep/xp-1))-FRIC), sub.index[j]


def copy_trades(dfs):
    rows=[]
    for lead,lag in [("SPY","^NDX"),("^NDX","SPY")]:
        dl,dg=dfs[lead],dfs[lag]; sig=dl["pullback_Signal"]
        r20l=dl["Close"].pct_change(20); r20g=dg["Close"].pct_change(20)
        pb=dl.get("P_bull"); pbr=dl.get("P_bear")
        for t in dl.index[sig!=0]:
            s=int(sig.loc[t]); conv=(pb.loc[t] if s>0 else pbr.loc[t])
            if conv!=conv or conv<0.65: continue
            if t not in r20g.index or not (r20g.loc[t]*s<r20l.loc[t]*s): continue
            mv,xt=sim_copy(dg,t,s,0.0375,0.075,585)
            if mv is None: continue
            rows.append({"et":xt,"pnl":mv*INITIAL*COPY_SIZE})
    return pd.DataFrame(rows)


def combined_rets(trade_df):
    d=trade_df.sort_values("et"); eq=INITIAL; out=[]
    for p in d["pnl"]:
        r=p/eq; out.append(r); eq*=(1+r)
    return np.array(out)


def block_mc(rets,days,lev,years=3):
    n=len(rets); tpy=n/max(days/365.25,0.1); nt=max(BLOCK,int(round(tpy*years)))
    nb=int(np.ceil(nt/BLOCK))
    finals=[]; dds=[]
    starts=RNG.integers(0,max(1,n-BLOCK),size=(N_PATHS,nb))
    for p in range(N_PATHS):
        seq=np.concatenate([rets[s:s+BLOCK] for s in starts[p]])[:nt]
        seq=np.clip(seq*lev,-0.95,None)
        eq=INITIAL*np.cumprod(1+seq)
        finals.append(eq[-1])
        dds.append(((eq-np.maximum.accumulate(eq))/np.maximum.accumulate(eq)).min())
    finals=np.array(finals); dds=np.array(dds)
    cagr=(finals/INITIAL)**(1/years)-1
    return {"cagr_p50":np.quantile(cagr,.5),"cagr_p5":np.quantile(cagr,.05),
            "dd_p5":np.quantile(dds,.05),"p_ruin":(finals<.5*INITIAL).mean(),
            "p_2x":(finals>2*INITIAL).mean()}


def main():
    print("="*100); print("  MONTE CARLO — system + safely-sized double-trade overlay (Part 8.48)")
    print("  block bootstrap (preserves correlated-loss clusters), copy size 15%, modest leverage")
    print("="*100)
    dfs={s:prep(s) for s in SYMBOLS}
    base=pd.concat([base_trades(s,dfs[s]) for s in SYMBOLS],ignore_index=True)
    copies=copy_trades(dfs)
    days=(base["et"].max()-base["et"].min()).days
    print(f"\n  base trades {len(base)} · copy trades {len(copies)} (15% size, conv>0.65, laggard)")

    base_r=combined_rets(base)
    both_r=combined_rets(pd.concat([base,copies],ignore_index=True))

    # realized
    def realized(r):
        eq=INITIAL;pk=INITIAL;dd=0
        for x in r: eq*=(1+x);pk=max(pk,eq);dd=min(dd,(eq-pk)/pk)
        yrs=max(days/365.25,.1)
        return (eq/INITIAL)**(1/yrs)-1, dd, eq/INITIAL-1
    bc,bd,bt_=realized(base_r); cc,cd,ct=realized(both_r)
    print(f"\n  ── REALIZED (after friction) ──")
    print(f"  base only        CAGR {bc*100:+.1f}%  DD {bd*100:+.1f}%  total {bt_*100:+.0f}%")
    print(f"  base + overlay   CAGR {cc*100:+.1f}%  DD {cd*100:+.1f}%  total {ct*100:+.0f}%  (+{(ct-bt_)*100:.0f}pp)")

    print(f"\n  ── BLOCK-BOOTSTRAP MC (3yr, 10k paths) ──")
    print(f"  {'config':<20}{'lev':>5}{'p50 CAGR':>10}{'p5 CAGR':>10}{'p5 DD':>9}{'P(ruin)':>9}{'P(2x)':>8}")
    print("  "+"-"*71)
    for label,r in [("base only",base_r),("base + overlay",both_r)]:
        for lev in [1.0,1.25,1.5]:
            m=block_mc(r,days,lev)
            print(f"  {label:<20}{lev:>5.2f}{m['cagr_p50']*100:>+9.1f}%{m['cagr_p5']*100:>+9.1f}%"
                  f"{m['dd_p5']*100:>+8.1f}%{m['p_ruin']*100:>8.2f}%{m['p_2x']*100:>+7.0f}%")
        print()

    print("  ── VERDICT ──")
    mb=block_mc(base_r,days,1.0); mo=block_mc(both_r,days,1.0)
    print(f"  Overlay adds {(mo['cagr_p50']-mb['cagr_p50'])*100:+.0f}pp p50 CAGR at 1× "
          f"(P(ruin) {mb['p_ruin']*100:.2f}% → {mo['p_ruin']*100:.2f}%, "
          f"p5 DD {mb['dd_p5']*100:.0f}% → {mo['dd_p5']*100:.0f}%)")


if __name__ == "__main__":
    main()
