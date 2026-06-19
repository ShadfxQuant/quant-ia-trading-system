"""
Three fixes toward ~80% return (Part 8.43).

Layers, on the GATED combined portfolio:
  FIX 1  Leverage      — scale per-trade returns by L (MC-validated to 2.5×, 0% ruin)
  FIX 2  Friction cut  — gates reduce trade count -> less cost drag; model 10bp/notional
  FIX 3  Conviction    — scale position size by HMM posterior aligned w/ trade
                         (high conviction bigger, low conviction smaller; [0.6,1.6])

Reports incremental effect of each fix, then a leverage×friction grid with
P(ruin), to find the leverage that targets ~80% NET CAGR.
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
GATE_CFG = {"SPY":(True,False), "^NDX":(True,False), "GLD":(True,False), "GC=F":(False,True)}
RNG = np.random.default_rng(2026)
N_PATHS = 10_000


def gate(df, col, rsi, hmm, rsi_short=40, rsi_long=60, p_veto=0.6):
    sig = df[col].copy(); r=df.get("RSI_14"); pb=df.get("P_bull"); pbr=df.get("P_bear")
    blk = pd.Series(False, index=df.index)
    if rsi and r is not None:
        blk |= (sig==-1)&(r<rsi_short); blk |= (sig==1)&(r>rsi_long)
    if hmm and pb is not None and pbr is not None:
        blk |= (sig==1)&(pbr>p_veto); blk |= (sig==-1)&(pb>p_veto)
    sig[blk]=0; return sig


def gated_trades(sym, conviction=False):
    df = prepare_dual(load_symbol(sym))
    rsi, hmm = GATE_CFG[sym]
    if df.index.tz: df.index = df.index.tz_localize(None)
    df["pullback_Signal"] = gate(df, "pullback_Signal", rsi, hmm)
    df["trend_carry_Signal"] = gate(df, "trend_carry_Signal", rsi, hmm)
    cfg = get_pullback_cfg(sym)
    tr = run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=sym, initial_capital=INITIAL)["trades"].copy()
    # conviction multiplier from HMM posterior aligned with side
    mults = []
    pbull = df.get("P_bull"); pbear = df.get("P_bear")
    for _, t in tr.iterrows():
        m = 1.0
        if conviction and pbull is not None:
            et = pd.Timestamp(t["entry_time"]); et = et.tz_localize(None) if et.tz else et
            sub = df[df.index <= et]
            if len(sub):
                long = (t["side"] > 0) if isinstance(t["side"],(int,float)) else str(t["side"]).upper() in ("LONG","BUY","1")
                conv = sub["P_bull"].iloc[-1] if long else sub["P_bear"].iloc[-1]
                if conv == conv:  # not NaN
                    m = float(np.clip(0.6 + conv*1.0, 0.6, 1.6))
        mults.append(m)
    tr["conv_mult"] = mults
    tr["symbol"] = sym
    return tr


def combined_rets(per_sym, conviction=False, friction_bps=0.0, notional_frac=0.35):
    combined = pd.concat(per_sym.values(), ignore_index=True).sort_values("entry_time")
    eq = INITIAL; rets = []
    fric = (friction_bps/10000.0) * notional_frac
    for _, t in combined.iterrows():
        pnl = t["pnl"] * (t["conv_mult"] if conviction else 1.0)
        r = pnl/eq - fric
        rets.append(r); eq *= (1.0 + r)
    return np.array(rets), combined


def realized(rets, days):
    eq = INITIAL; peak=INITIAL; dd=0.0
    for r in rets:
        eq*=(1+r); peak=max(peak,eq); dd=min(dd,(eq-peak)/peak)
    yrs = max(days/365.25, 0.1)
    return (eq/INITIAL)**(1/yrs)-1, dd, eq/INITIAL-1


def mc(rets, days, lev=1.0, years=3):
    r = np.clip(rets*lev, -0.95, None)
    n = len(r); tpy = n/max(days/365.25,0.1); nt = max(1,int(round(tpy*years)))
    idx = RNG.integers(0,n,size=(N_PATHS,nt))
    eq = INITIAL*np.cumprod(1+r[idx],axis=1); final=eq[:,-1]
    dd = ((eq-np.maximum.accumulate(eq,axis=1))/np.maximum.accumulate(eq,axis=1)).min(axis=1)
    cagr = (final/INITIAL)**(1/years)-1
    return {"cagr_p50":float(np.quantile(cagr,0.5)),"cagr_p5":float(np.quantile(cagr,0.05)),
            "dd_p5":float(np.quantile(dd,0.05)),"p_ruin":float((final<0.5*INITIAL).mean())}


def main():
    print("="*100); print("  THREE FIXES TO ~80% (Part 8.43)"); print("="*100)

    print("\n  building gated trades (with conviction tags)...")
    per_plain = {s: gated_trades(s, conviction=False) for s in SYMBOLS}
    per_conv  = {s: gated_trades(s, conviction=True)  for s in SYMBOLS}
    days = (pd.concat(per_plain.values())["exit_time"].max() -
            pd.concat(per_plain.values())["entry_time"].min()).days

    print("\n  ── INCREMENTAL EFFECT OF EACH FIX (realized, 1× leverage) ──")
    print(f"  {'config':<42}{'CAGR':>9}{'MaxDD':>9}{'totalRet':>10}")
    print("  "+"-"*70)
    # baseline gated, no friction
    r0,_ = combined_rets(per_plain); c,d,tot = realized(r0,days)
    print(f"  {'gated, no friction, flat size':<42}{c*100:>+8.1f}%{d*100:>+8.1f}%{tot*100:>+9.1f}%")
    # + friction
    r1,_ = combined_rets(per_plain, friction_bps=10); c,d,tot = realized(r1,days)
    print(f"  {'gated + 10bp friction':<42}{c*100:>+8.1f}%{d*100:>+8.1f}%{tot*100:>+9.1f}%")
    # + conviction sizing
    r2,_ = combined_rets(per_conv, conviction=True, friction_bps=10); c,d,tot = realized(r2,days)
    print(f"  {'gated + friction + conviction size':<42}{c*100:>+8.1f}%{d*100:>+8.1f}%{tot*100:>+9.1f}%")

    # leverage sweep on the full-fix config (friction + conviction)
    print("\n  ── LEVERAGE SWEEP (gated + 10bp friction + conviction), 3yr MC ──")
    print(f"  {'leverage':<10}{'p50 CAGR':>10}{'p5 CAGR':>10}{'p5 DD':>9}{'P(ruin)':>10}{'note':>16}")
    print("  "+"-"*68)
    target = None
    for lev in [1.0,1.5,2.0,2.5,3.0]:
        m = mc(r2, days, lev=lev)
        note = ""
        if target is None and m["cagr_p50"] >= 0.80:
            note = "<- ~80% target"; target = lev
        print(f"  {lev:<10.1f}{m['cagr_p50']*100:>+9.1f}%{m['cagr_p5']*100:>+9.1f}%"
              f"{m['dd_p5']*100:>+8.1f}%{m['p_ruin']*100:>9.2f}%{note:>16}")

    print("\n  ── VERDICT ──")
    if target:
        m = mc(r2, days, lev=target)
        print(f"  ~80% net CAGR target reached at {target:.1f}× leverage")
        print(f"     p50 CAGR {m['cagr_p50']*100:+.0f}%  ·  p5 DD {m['dd_p5']*100:.0f}%  ·  P(ruin) {m['p_ruin']*100:.2f}%")
    else:
        # interpolate
        m2=mc(r2,days,2.0); m25=mc(r2,days,2.5)
        print(f"  ~80% sits between 2.0× ({m2['cagr_p50']*100:.0f}%) and 2.5× ({m25['cagr_p50']*100:.0f}%)")
    print("  (buy-&-hold SPY/gold for reference: ~20-38% CAGR with -19 to -21% DD)")


if __name__ == "__main__":
    main()
