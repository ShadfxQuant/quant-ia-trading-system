"""
Tier 1 + Tier 2 gates + SP500/NASDAQ double-trade overlay (Part 8.46).

TIER 1: RSI gate (global) + HMM veto (GC=F only)
TIER 2: regime suppression (stand down in 'stabilization' or P_range>0.6) +
        trend_carry guarded (gates applied to BOTH sleeves)

DOUBLE-TRADE (new): SPY<->^NDX correlate 0.95. When a STRONG, high-conviction
entry fires on the LEADING (non-trailing) one, copy it onto the TRAILING one
with WIDER TP/SL so the laggard has room to catch up. Only on confirmed-strong
setups (HMM conviction > 0.65 AND passes gates).
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

INITIAL = 100_000.0
SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
GATE = {"SPY":(True,False),"^NDX":(True,False),"GLD":(True,False),"GC=F":(False,True)}


def gate(df, col, rsi, hmm, regime_supp=False):
    sig=df[col].copy(); r=df.get("RSI_14"); pb=df.get("P_bull"); pbr=df.get("P_bear")
    blk=pd.Series(False,index=df.index)
    if rsi and r is not None: blk|=(sig==-1)&(r<40); blk|=(sig==1)&(r>60)
    if hmm and pb is not None and pbr is not None: blk|=(sig==1)&(pbr>0.6); blk|=(sig==-1)&(pb>0.6)
    if regime_supp:
        reg=df.get("Regime"); prg=df.get("P_range")
        if reg is not None: blk|=(sig!=0)&reg.astype(str).str.contains("stabiliz",case=False,na=False)
        if prg is not None: blk|=(sig!=0)&(prg>0.6)
    sig[blk]=0; return sig


def bt(df, sym):
    cfg=get_pullback_cfg(sym)
    tr=run_portfolio(df,[StrategySpec("pullback",cfg,pb_exit(cfg)),
                         StrategySpec("trend_carry",TRENDCARRY,tc_exit())],
                     symbol=sym,initial_capital=INITIAL)["trades"]
    if len(tr)==0: return None,None
    eq=INITIAL;peak=INITIAL;dd=0.0
    for p in tr["pnl"]: eq+=p;peak=max(peak,eq);dd=min(dd,(eq-peak)/peak)
    days=(tr["exit_time"].max()-tr["entry_time"].min()).days; yrs=max(days/365.25,0.1)
    wins=tr[tr["pnl"]>0];loss=tr[tr["pnl"]<0]
    pf=wins["pnl"].sum()/(-loss["pnl"].sum()) if len(loss) and loss["pnl"].sum()<0 else 999
    return {"n":len(tr),"wr":(tr["pnl"]>0).mean()*100,"pf":pf,
            "cagr":((eq/INITIAL)**(1/yrs)-1)*100,"dd":dd*100,"profit":eq-INITIAL}, tr


def prep_gated(sym, tier2=False):
    df=prepare_dual(load_symbol(sym))
    if df.index.tz: df.index=df.index.tz_localize(None)
    rsi,hmm=GATE[sym]
    df["pullback_Signal"]=gate(df,"pullback_Signal",rsi,hmm,regime_supp=tier2)
    df["trend_carry_Signal"]=gate(df,"trend_carry_Signal",rsi,hmm,regime_supp=tier2)
    return df


def simulate_copy(dfB, entry_time, side, stop_pct, tp_pct, max_bars):
    """Walk B's bars from entry; exit on wider TP/SL/time. Return pct move (signed for side)."""
    sub=dfB[dfB.index>=entry_time]
    if len(sub)<2: return None
    ep=sub["Close"].iloc[0]
    for i in range(1,min(len(sub),max_bars)):
        hi=sub["High"].iloc[i]; lo=sub["Low"].iloc[i]
        if side>0:
            if lo<=ep*(1-stop_pct): return -stop_pct
            if hi>=ep*(1+tp_pct): return tp_pct
        else:
            if hi>=ep*(1+stop_pct): return -stop_pct
            if lo<=ep*(1-tp_pct): return tp_pct
    # time exit
    xp=sub["Close"].iloc[min(len(sub)-1,max_bars-1)]
    return (xp/ep-1) if side>0 else (ep/xp-1)


def main():
    print("="*100); print("  TIER 1+2 GATES + SP500/NASDAQ DOUBLE-TRADE (Part 8.46)"); print("="*100)

    # ---- Tier 1 vs Tier 1+2 ----
    print("\n  ── TIER 1 (gates) vs TIER 1+2 (+regime suppression+tc guard) ──")
    print(f"  {'symbol':<8}{'config':<12}{'nTrades':>9}{'WR':>7}{'PF':>7}{'CAGR':>9}{'DD':>8}{'profit':>11}")
    print("  "+"-"*70)
    dfs_t1={}; dfs_t2={}; tot={"t1":0.0,"t2":0.0,"t1n":0,"t2n":0}
    for sym in SYMBOLS:
        d1=prep_gated(sym,tier2=False); m1,_=bt(d1,sym); dfs_t1[sym]=d1
        d2=prep_gated(sym,tier2=True);  m2,_=bt(d2,sym); dfs_t2[sym]=d2
        print(f"  {sym:<8}{'Tier 1':<12}{m1['n']:>9}{m1['wr']:>6.1f}%{m1['pf']:>7.2f}{m1['cagr']:>+8.1f}%{m1['dd']:>+7.1f}%{m1['profit']:>+11,.0f}")
        print(f"  {'':<8}{'Tier 1+2':<12}{m2['n']:>9}{m2['wr']:>6.1f}%{m2['pf']:>7.2f}{m2['cagr']:>+8.1f}%{m2['dd']:>+7.1f}%{m2['profit']:>+11,.0f}")
        tot["t1"]+=m1["profit"];tot["t2"]+=m2["profit"];tot["t1n"]+=m1["n"];tot["t2n"]+=m2["n"]
    print("  "+"-"*70)
    print(f"  {'TOTAL':<8}{'Tier 1':<12}{tot['t1n']:>9}{'':>22}{tot['t1']:>+33,.0f}")
    print(f"  {'':<8}{'Tier 1+2':<12}{tot['t2n']:>9}{'':>22}{tot['t2']:>+33,.0f}")

    # ---- Double-trade overlay on SPY <-> ^NDX (use Tier 1 dfs) ----
    print("\n  ── DOUBLE-TRADE: SP500 <-> NASDAQ lead-lag copy (Tier 1 base) ──")
    A,B="SPY","^NDX"
    dfA,dfB=dfs_t1[A],dfs_t1[B]
    # strong entries: gated signal != 0 AND HMM conviction aligned > 0.65
    copies=[]
    for lead,lag,dfl,dfg in [(A,B,dfA,dfB),(B,A,dfB,dfA)]:
        sig=dfl["pullback_Signal"]
        ret20l=dfl["Close"].pct_change(20); ret20g=dfg["Close"].pct_change(20)
        pb=dfl.get("P_bull"); pbr=dfl.get("P_bear")
        strong_idx=dfl.index[(sig!=0)]
        for t in strong_idx:
            s=int(sig.loc[t]); conv=(pb.loc[t] if s>0 else pbr.loc[t])
            if conv!=conv or conv<0.65: continue          # require strong conviction
            if t not in ret20g.index: continue
            # lag must be TRAILING the leader (lower recent return in trade direction)
            la=ret20l.loc[t]*s; lg=ret20g.loc[t]*s
            if not (lg<la): continue                        # B not trailing -> skip
            mv=simulate_copy(dfg, t, s, stop_pct=0.0375, tp_pct=0.075, max_bars=585)  # 1.5x wider
            if mv is None: continue
            copies.append({"lead":lead,"lag":lag,"side":s,"conv":float(conv),"move":mv,
                           "pnl":mv*INITIAL*0.30})
    cp=pd.DataFrame(copies)
    if len(cp):
        wr=(cp["pnl"]>0).mean()*100; tot_pnl=cp["pnl"].sum()
        wins=cp[cp["pnl"]>0]["pnl"].sum(); loss=-cp[cp["pnl"]<0]["pnl"].sum()
        pf=wins/loss if loss>0 else 999
        print(f"  copy trades: {len(cp)}  WR {wr:.1f}%  PF {pf:.2f}  total PnL ${tot_pnl:+,.0f}")
        print(f"  avg conviction {cp['conv'].mean():.2f}  ·  avg move {cp['move'].mean()*100:+.2f}%")
        print(f"  by direction: long {len(cp[cp.side>0])}  short {len(cp[cp.side<0])}")
    # SPY+^NDX tier1 profit
    spyndx=0
    for sym in [A,B]:
        m,_=bt(dfs_t1[sym],sym); spyndx+=m["profit"]
    print(f"\n  SP500+NASDAQ (Tier 1) base profit: ${spyndx:+,.0f}")
    if len(cp):
        print(f"  + double-trade overlay PnL:         ${cp['pnl'].sum():+,.0f}")
        print(f"  = combined:                         ${spyndx+cp['pnl'].sum():+,.0f}  "
              f"({(cp['pnl'].sum())/spyndx*100:+.1f}% uplift)")
        print(f"\n  NOTE: copy trades deliberately ADD correlated exposure (SPY/^NDX corr 0.95);")
        print(f"  only fire on conviction>0.65 strong setups + laggard confirmation to mitigate.")


if __name__ == "__main__":
    main()
