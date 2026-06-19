"""
Does the double-trade survive? + volume/orderflow confirmation filters (Part 8.47).

A. DOUBLE-TRADE STRESS: re-run SPY/^NDX copy trades WITH 10bp friction, build
   the copy-trade equity curve, report max drawdown + worst correlated cluster.
B. VOLUME confirmation filter: require RVOL > 1.0 on entry bar (institutional
   participation). Backtest base engine + filter, compare.
C. CVD / orderflow confirmation filter: require CVD slope aligned with trade
   direction. Backtest + compare.
D. DOUBLE-TRADE + volume confirm: does requiring a volume surge on the leader
   improve the copy trades?
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
from research.proxies import cvd_proxy

INITIAL = 100_000.0
SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
GATE = {"SPY":(True,False),"^NDX":(True,False),"GLD":(True,False),"GC=F":(False,True)}
FRIC = (10/10000.0)*0.30


def gate(df, col, rsi, hmm):
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


def bt(df, sym):
    cfg=get_pullback_cfg(sym)
    tr=run_portfolio(df,[StrategySpec("pullback",cfg,pb_exit(cfg)),
                         StrategySpec("trend_carry",TRENDCARRY,tc_exit())],
                     symbol=sym,initial_capital=INITIAL)["trades"]
    if len(tr)==0: return {"n":0,"wr":0,"pf":0,"profit":0}
    wins=tr[tr["pnl"]>0];loss=tr[tr["pnl"]<0]
    pf=wins["pnl"].sum()/(-loss["pnl"].sum()) if len(loss) and loss["pnl"].sum()<0 else 999
    return {"n":len(tr),"wr":(tr["pnl"]>0).mean()*100,"pf":pf,"profit":tr["pnl"].sum()}


def apply_filter(df, mask_fn):
    """Zero out entry signals where the confirmation mask is False."""
    out=df.copy()
    m=mask_fn(out)
    for col in ["pullback_Signal","trend_carry_Signal"]:
        s=out[col].copy()
        s[(s!=0)&(~m)]=0
        out[col]=s
    return out


def simulate_copy(dfB, t, side, stop, tp, maxb, fric=0.0):
    sub=dfB[dfB.index>=t]
    if len(sub)<2: return None
    ep=sub["Close"].iloc[0]
    for i in range(1,min(len(sub),maxb)):
        hi=sub["High"].iloc[i]; lo=sub["Low"].iloc[i]
        if side>0:
            if lo<=ep*(1-stop): return -stop-fric
            if hi>=ep*(1+tp): return tp-fric
        else:
            if hi>=ep*(1+stop): return -stop-fric
            if lo<=ep*(1-tp): return tp-fric
    xp=sub["Close"].iloc[min(len(sub)-1,maxb-1)]
    return ((xp/ep-1) if side>0 else (ep/xp-1))-fric


def main():
    print("="*100); print("  VOLUME / ORDERFLOW + DOUBLE-TRADE STRESS (Part 8.47)"); print("="*100)
    dfs={s:prep(s) for s in SYMBOLS}

    # ---------- A. double-trade with friction + drawdown ----------
    print("\n  ── A. DOUBLE-TRADE under 10bp friction + drawdown stress ──")
    A,B="SPY","^NDX"; dfA,dfB=dfs[A],dfs[B]
    copies=[]
    for dl,dg in [(dfA,dfB),(dfB,dfA)]:
        sig=dl["pullback_Signal"]; r20l=dl["Close"].pct_change(20); r20g=dg["Close"].pct_change(20)
        pb=dl.get("P_bull"); pbr=dl.get("P_bear")
        rvol=dl.get("RVOL")
        for t in dl.index[sig!=0]:
            s=int(sig.loc[t]); conv=(pb.loc[t] if s>0 else pbr.loc[t])
            if conv!=conv or conv<0.65: continue
            if t not in r20g.index or not (r20g.loc[t]*s < r20l.loc[t]*s): continue
            mv=simulate_copy(dg,t,s,0.0375,0.075,585,fric=FRIC)
            if mv is None: continue
            rv = float(rvol.loc[t]) if rvol is not None and t in rvol.index and rvol.loc[t]==rvol.loc[t] else 1.0
            copies.append({"t":t,"side":s,"move":mv,"pnl":mv*INITIAL*0.30,"rvol":rv})
    cp=pd.DataFrame(copies).sort_values("t")
    # equity curve + max DD
    eq=INITIAL;peak=INITIAL;ddm=0.0
    for p in cp["pnl"]: eq+=p;peak=max(peak,eq);ddm=min(ddm,(eq-peak)/peak)
    wr=(cp["pnl"]>0).mean()*100; pf=cp[cp.pnl>0].pnl.sum()/(-cp[cp.pnl<0].pnl.sum())
    # worst 10-trade rolling cluster (crash stress proxy)
    worst=cp["pnl"].rolling(10).sum().min()
    print(f"  copies {len(cp)}  WR {wr:.1f}%  PF {pf:.2f}  net PnL ${cp['pnl'].sum():+,.0f} (after friction)")
    print(f"  copy-book max drawdown {ddm*100:+.1f}%  ·  worst 10-trade cluster ${worst:+,.0f}")

    # ---------- B. volume confirmation filter ----------
    print("\n  ── B. VOLUME confirmation (require RVOL > 1.0 on entry) ──")
    print(f"  {'symbol':<8}{'base profit':>13}{'base PF':>9}{'+vol profit':>13}{'+vol PF':>9}{'+vol n':>8}{'base n':>8}")
    print("  "+"-"*68)
    bt1=0; btv=0
    for s in SYMBOLS:
        base=bt(dfs[s],s)
        fv=apply_filter(dfs[s], lambda d: d["RVOL"].fillna(1.0)>1.0)
        mv=bt(fv,s)
        bt1+=base["profit"]; btv+=mv["profit"]
        print(f"  {s:<8}{base['profit']:>+13,.0f}{base['pf']:>9.2f}{mv['profit']:>+13,.0f}{mv['pf']:>9.2f}{mv['n']:>8}{base['n']:>8}")
    print("  "+"-"*68)
    print(f"  {'TOTAL':<8}{bt1:>+13,.0f}{'':>9}{btv:>+13,.0f}  (vol filter Δ ${btv-bt1:+,.0f})")

    # ---------- C. CVD orderflow confirmation ----------
    print("\n  ── C. CVD orderflow confirmation (CVD slope aligned with trade) ──")
    print(f"  {'symbol':<8}{'base profit':>13}{'+cvd profit':>13}{'+cvd PF':>9}{'+cvd n':>8}{'base n':>8}")
    print("  "+"-"*60)
    btc=0
    def cvd_mask(d):
        cvd=cvd_proxy(d); slope=cvd-cvd.shift(20)
        # aligned: long wants slope>0, short wants slope<0; combine per-signal below
        return slope
    for s in SYMBOLS:
        base=bt(dfs[s],s)
        d=dfs[s].copy(); cvd=cvd_proxy(d); slope=(cvd-cvd.shift(20)).fillna(0)
        for col in ["pullback_Signal","trend_carry_Signal"]:
            sig=d[col].copy()
            bad=((sig==1)&(slope<0))|((sig==-1)&(slope>0))
            sig[bad]=0; d[col]=sig
        mc=bt(d,s); btc+=mc["profit"]
        print(f"  {s:<8}{base['profit']:>+13,.0f}{mc['profit']:>+13,.0f}{mc['pf']:>9.2f}{mc['n']:>8}{base['n']:>8}")
    print("  "+"-"*60)
    print(f"  {'TOTAL':<8}{bt1:>+13,.0f}{btc:>+13,.0f}  (CVD filter Δ ${btc-bt1:+,.0f})")

    # ---------- D. double-trade + volume confirm on leader ----------
    print("\n  ── D. DOUBLE-TRADE requiring RVOL>1.2 surge on leader entry ──")
    hi_vol=cp[cp["rvol"]>1.2]
    if len(hi_vol):
        wr2=(hi_vol["pnl"]>0).mean()*100
        pf2=hi_vol[hi_vol.pnl>0].pnl.sum()/max(-hi_vol[hi_vol.pnl<0].pnl.sum(),1)
        print(f"  vol-confirmed copies {len(hi_vol)}/{len(cp)}  WR {wr2:.1f}%  PF {pf2:.2f}  "
              f"PnL ${hi_vol['pnl'].sum():+,.0f}")
        print(f"  (vs all copies: WR {wr:.1f}%, PnL ${cp['pnl'].sum():+,.0f})")


if __name__ == "__main__":
    main()
