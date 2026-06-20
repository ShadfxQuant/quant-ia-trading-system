"""
Does the system work on shorter timeframes? (Part 8.50)

Runs the gated engine on SPY at 1h / 15m / 5m over each interval's available
window. Reports raw metrics AND friction-adjusted, because the killer on short
TFs is trade frequency × cost. yfinance caps intraday history (5m/15m=60d),
so short-TF samples are small — read directionally.
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
SYM = "SPY"
# bars per year by interval (approx, RTH): 1h~1638, 15m~6552, 5m~19656
BARS_PER_YEAR = {"1h": 1638, "15m": 6552, "5m": 19656}


def gate(df, col):
    sig = df[col].copy(); r = df.get("RSI_14")
    blk = pd.Series(False, index=df.index)
    if r is not None:
        blk |= (sig == -1) & (r < 40); blk |= (sig == 1) & (r > 60)
    sig[blk] = 0; return sig


def run(interval):
    df = prepare_dual(load_symbol(SYM, interval=interval))
    df["pullback_Signal"] = gate(df, "pullback_Signal")
    df["trend_carry_Signal"] = gate(df, "trend_carry_Signal")
    cfg = get_pullback_cfg(SYM)
    tr = run_portfolio(df, [StrategySpec("pullback", cfg, pb_exit(cfg)),
                            StrategySpec("trend_carry", TRENDCARRY, tc_exit())],
                       symbol=SYM, initial_capital=INITIAL)["trades"]
    if len(tr) == 0: return None
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    yrs = max(days/365.25, 0.05)
    trades_per_year = len(tr)/yrs
    wins = tr[tr["pnl"]>0]; loss = tr[tr["pnl"]<0]
    pf = wins["pnl"].sum()/(-loss["pnl"].sum()) if len(loss) and loss["pnl"].sum()<0 else 999
    wr = (tr["pnl"]>0).mean()*100
    # raw equity
    eq=INITIAL;peak=INITIAL;dd=0
    for p in tr["pnl"]: eq+=p;peak=max(peak,eq);dd=min(dd,(eq-peak)/peak)
    cagr=((eq/INITIAL)**(1/yrs)-1)*100
    # friction-adjusted: 10bp of notional (~30%) per trade
    fric_per_trade = 0.0010*0.30*INITIAL   # $ per trade
    eqf=INITIAL;ddf=0;pkf=INITIAL
    for p in tr["pnl"]:
        eqf+=p-fric_per_trade; pkf=max(pkf,eqf); ddf=min(ddf,(eqf-pkf)/pkf)
    cagrf=((max(eqf,1)/INITIAL)**(1/yrs)-1)*100
    return {"interval":interval,"n":len(tr),"tpy":trades_per_year,"days":days,
            "pf":pf,"wr":wr,"cagr":cagr,"dd":dd*100,"cagrf":cagrf,"profit":eq-INITIAL,"profitf":eqf-INITIAL}


def main():
    print("="*92)
    print(f"  DOES THE SYSTEM WORK ON SHORTER TIMEFRAMES?  (SPY, gated engine)  — Part 8.50")
    print("="*92)
    print(f"  {'TF':<6}{'window':>8}{'nTrades':>9}{'tr/yr':>8}{'WR':>7}{'PF':>7}"
          f"{'CAGR raw':>10}{'CAGR +10bp':>12}{'profit raw':>12}{'profit +fric':>14}")
    print("  "+"-"*90)
    rows=[]
    for iv in ["1h","15m","5m"]:
        try:
            r=run(iv)
            if r is None: print(f"  {iv:<6} no trades"); continue
            rows.append(r)
            print(f"  {iv:<6}{r['days']:>6}d{r['n']:>9}{r['tpy']:>8.0f}{r['wr']:>6.1f}%{r['pf']:>7.2f}"
                  f"{r['cagr']:>+9.1f}%{r['cagrf']:>+11.1f}%{r['profit']:>+12,.0f}{r['profitf']:>+14,.0f}")
        except Exception as e:
            print(f"  {iv:<6} ERROR: {type(e).__name__}: {str(e)[:50]}")

    print("\n  ── READ ──")
    if len(rows)>=2:
        h=next((x for x in rows if x['interval']=='1h'),None)
        for r in rows:
            if r['interval']=='1h': continue
            mult=r['tpy']/h['tpy'] if h and h['tpy'] else 0
            print(f"  {r['interval']}: {mult:.1f}× more trades/yr than 1h → "
                  f"friction turns CAGR {r['cagr']:+.0f}% into {r['cagrf']:+.0f}%")
    print("  (intraday samples are ~60 days — small; treat as directional, not definitive)")


if __name__ == "__main__":
    main()
