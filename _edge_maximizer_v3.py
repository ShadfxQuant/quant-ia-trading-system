"""
Edge maximizer v3 — the honest ceiling (Part 8.34c).

v1: diversified trend+MR capped at Sharpe ~1.2.
v2: advanced market-neutral math FAILED (negative) — it fights the bull-regime
    beta that was the actual edge. Documented negative result.

v3 tests the two *principled* remaining levers (no overfitting fishing):
  A. Regime gate — only run the trend book when trends exist (asset above
     200-day MA AND not in a vol spike). Sit out chop.
  B. Controlled leverage on the vol-targeted book — DD is bounded by vol
     targeting, so leverage scales CAGR while P(ruin) stays low (consistent
     with _montecarlo_final's 0% ruin to 2.5×).
And benchmarks everything against buy-&-hold SPY and the realized production
pullback engine numbers.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd
from core.data_loader import load_symbol

UNIVERSE = {
    "INDEX":  ["SPY", "^NDX", "^GSPC", "DIA", "QQQ", "IWM", "MDY"],
    "FUTURE": ["ES=F", "NQ=F", "YM=F", "GC=F", "SI=F", "HG=F", "CL=F", "NG=F"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD"],
    "STOCK":  ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "TSLA", "AMZN", "AVGO"],
    "COMMOD": ["GLD", "SLV", "USO", "DBC", "UNG"],
}
ALL = [s for v in UNIVERSE.values() for s in v]
TD = 252

def _rsi(c, p=14):
    d = c.diff(); g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    return (100 - 100/(1+g/l.replace(0,np.nan))).fillna(50)

def metrics(r):
    r = r.fillna(0)
    if r.std()==0 or r.abs().sum()==0: return None
    sh = r.mean()/r.std()*np.sqrt(TD)
    eq=(1+r).cumprod(); total=float(eq.iloc[-1]); yrs=len(r)/TD
    cagr=total**(1/yrs)-1 if total>0 else -1
    dd=float(((eq-eq.cummax())/eq.cummax()).min())
    act=r[r!=0]; wr=float((act>0).mean()) if len(act) else 0
    sortino=r.mean()/r[r<0].std()*np.sqrt(TD) if (r<0).any() else np.inf
    calmar=cagr/abs(dd) if dd!=0 else np.inf
    return {"sharpe":sh,"sortino":sortino,"cagr":cagr,"maxdd":dd,"wr":wr,"calmar":calmar}

def show(lbl,m):
    if m is None: print(f"  {lbl:<44} (none)"); return
    print(f"  {lbl:<44} Sharpe {m['sharpe']:>5.2f}  CAGR {m['cagr']*100:>+6.1f}%  "
          f"DD {m['maxdd']*100:>+6.1f}%  WR {m['wr']*100:>4.1f}%  Calmar {m['calmar']:>4.2f}")

def vol_target(r,target=0.12,span=30):
    rv=r.ewm(span=span).std()*np.sqrt(TD)
    return r*(target/rv).clip(0,3).shift(1).fillna(0)

def main():
    print("="*100); print("  EDGE MAXIMIZER v3 — honest ceiling + principled levers (Part 8.34c)"); print("="*100)
    daily={}
    for s in ALL:
        try:
            df=load_symbol(s)
            d=df.resample("1D").agg({"Open":"first","High":"max","Low":"min","Close":"last"}).dropna()
            if len(d)>260: daily[s]=d
        except Exception: pass
    print(f"  {len(daily)} symbols\n")

    # build trend + MR sleeve returns, with optional regime gate
    def book(gate=False):
        trend_cols, mr_cols = {}, {}
        for sym,df in daily.items():
            c=df["Close"]; dret=c.pct_change()
            ma200=c.rolling(200).mean(); ma50=c.rolling(50).mean()
            rvol=dret.rolling(20).std(); rvol_med=rvol.rolling(120).median()
            # regime gate: trade trend only above 200MA and vol not spiking
            ok = pd.Series(1.0, index=df.index)
            if gate:
                ok = ((c>ma200) & (rvol < rvol_med*1.5)).astype(float)
            # trend = golden cross; dualmomo
            gc=(ma50>ma200).astype(float)
            dm=(c.pct_change(120)>0).astype(float)
            trend_cols[f"gc:{sym}"]=((gc*ok).shift(1)*dret).fillna(0)
            trend_cols[f"dm:{sym}"]=((dm*ok).shift(1)*dret).fillna(0)
            # MR = connors rsi2
            rsi2=_rsi(c,2); pos=pd.Series(np.nan,index=df.index)
            pos[(rsi2<10)&(c>ma200)]=1; pos[rsi2>70]=0
            mr_cols[f"cr:{sym}"]=(pos.ffill().fillna(0).shift(1)*dret).fillna(0)
        T=pd.DataFrame(trend_cols); M=pd.DataFrame(mr_cols)
        # inverse-vol weight each sleeve (lagged rolling vol, no lookahead)
        def ivw(R):
            v=R.rolling(60).std().shift(1); w=1/v.replace(0,np.nan)
            w=w.div(w.sum(axis=1),axis=0); return (R*w).sum(axis=1)
        t=ivw(T); m=ivw(M)
        a=pd.concat([t.rename("t"),m.rename("m")],axis=1).fillna(0)
        sv_t,sv_m=a["t"].std(),a["m"].std()
        wt=(1/sv_t)/((1/sv_t)+(1/sv_m))
        erc=wt*a["t"]+(1-wt)*a["m"]
        return erc

    print("── BASELINE (v1 reproduction): ungated ERC trend+MR ──")
    erc=book(gate=False); show("ungated ERC", metrics(erc)); show("  vol-targeted 12%", metrics(vol_target(erc,0.12)))

    print("\n── LEVER A: regime-gated trend book ──")
    erc_g=book(gate=True); show("regime-gated ERC", metrics(erc_g))
    erc_g_vt=vol_target(erc_g,0.12); show("  gated + vol-targeted 12%", metrics(erc_g_vt))

    print("\n── LEVER B: controlled leverage on best vol-targeted book ──")
    base = vol_target(erc_g,0.12) if metrics(erc_g)["sharpe"]>metrics(erc)["sharpe"] else vol_target(erc,0.12)
    for lev in (1.0,1.5,2.0,2.5,3.0):
        show(f"  leverage {lev:.1f}×", metrics(base*lev))

    print("\n── BENCHMARKS ──")
    spy=load_symbol("SPY")["Close"].resample("1D").last().pct_change()
    show("buy & hold SPY", metrics(spy))
    print("  production pullback engine (realized, per-Part 8.30):")
    print("     SPY Sharpe~2.5  CAGR +20%  DD -6.5%  per-trade WR 71%  PF 3.5")

    print("\n"+"="*100); print("  VERDICT"); print("="*100)
    b=metrics(base)
    print(f"  Best diversified portfolio (vol-targeted, gated): "
          f"Sharpe {b['sharpe']:.2f}, CAGR {b['cagr']*100:+.1f}%, DD {b['maxdd']*100:+.1f}%")
    print(f"  At 2× leverage: Sharpe {b['sharpe']:.2f} (same), "
          f"CAGR {metrics(base*2)['cagr']*100:+.1f}%, DD {metrics(base*2)['maxdd']*100:+.1f}%")


if __name__ == "__main__":
    main()
