"""
Three-way comparison: GATED engine vs PRODUCTION vs BUY-&-HOLD (Part 8.42).

Gated = the two agreed gates, each scoped to where the 8.41 backtest showed
it helps:
  - SPY / ^NDX / GLD : RSI gate  (block short<RSI40, long>RSI60)
  - GC=F            : HMM veto   (block long if P_bear>.6, short if P_bull>.6)

Daily calendar equity curves + WR / CAGR / total return / DD / PF for each
of the three on every symbol. Emits research/results/three_way.json.
"""
from __future__ import annotations
import warnings, logging, json, os
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
GATE_CFG = {
    "SPY":  dict(rsi=True,  hmm=False),
    "^NDX": dict(rsi=True,  hmm=False),
    "GLD":  dict(rsi=True,  hmm=False),
    "GC=F": dict(rsi=False, hmm=True),
}


def gate(df, col, rsi=False, hmm=False, rsi_short=40, rsi_long=60, p_veto=0.6):
    sig = df[col].copy()
    r = df.get("RSI_14"); pb = df.get("P_bull"); pbr = df.get("P_bear")
    blk = pd.Series(False, index=df.index)
    if rsi and r is not None:
        blk |= (sig == -1) & (r < rsi_short)
        blk |= (sig == 1) & (r > rsi_long)
    if hmm and pb is not None and pbr is not None:
        blk |= (sig == 1) & (pbr > p_veto)
        blk |= (sig == -1) & (pb > p_veto)
    sig[blk] = 0
    return sig


def trades_for(df, sym):
    cfg = get_pullback_cfg(sym)
    return run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=sym, initial_capital=INITIAL)["trades"]


def daily_eq(trades, idx):
    if len(trades) == 0: return pd.Series(INITIAL, index=idx)
    by_day = trades.groupby(pd.to_datetime(trades["exit_time"]).dt.normalize())["pnl"].sum()
    return (INITIAL + by_day.cumsum()).reindex(idx, method="ffill").fillna(INITIAL)


def metrics(eq, trades=None):
    eq = eq.dropna()
    total = eq.iloc[-1]/INITIAL - 1
    yrs = max((eq.index[-1]-eq.index[0]).days/365.25, 0.1)
    cagr = (eq.iloc[-1]/INITIAL)**(1/yrs)-1
    dd = float(((eq-eq.cummax())/eq.cummax()).min())
    out = {"total": round(total*100,1), "cagr": round(cagr*100,1), "dd": round(dd*100,1)}
    if trades is not None and len(trades):
        out["wr"] = round((trades["pnl"]>0).mean()*100,1)
        wins=trades[trades["pnl"]>0]["pnl"].sum(); loss=-trades[trades["pnl"]<0]["pnl"].sum()
        out["pf"] = round(wins/loss,2) if loss>0 else 999
        out["n"] = len(trades)
    else:
        out["wr"]=None; out["pf"]=None; out["n"]=None
    return out


def ds(s, n=90):
    s = s.dropna()
    if len(s) <= n: return [round(x) for x in s.tolist()]
    st = len(s)/n
    return [round(s.iloc[min(int(i*st), len(s)-1)]) for i in range(n)]


def main():
    print("="*100)
    print("  GATED vs PRODUCTION vs BUY-&-HOLD (Part 8.42)")
    print("="*100)
    out = {"symbols": {}}
    agg = {"prod":0.0, "gated":0.0}
    for sym in SYMBOLS:
        raw = load_symbol(sym)
        base = prepare_dual(raw)

        prod_tr = trades_for(base.copy(), sym)
        g = base.copy()
        g["pullback_Signal"] = gate(g, "pullback_Signal", **GATE_CFG[sym])
        g["trend_carry_Signal"] = gate(g, "trend_carry_Signal", **GATE_CFG[sym])
        gated_tr = trades_for(g, sym)

        t0 = min(pd.to_datetime(prod_tr["entry_time"]).min(), pd.to_datetime(gated_tr["entry_time"]).min()).normalize()
        t1 = max(pd.to_datetime(prod_tr["exit_time"]).max(), pd.to_datetime(gated_tr["exit_time"]).max()).normalize()
        idx = pd.date_range(t0, t1, freq="D")

        eq_p = daily_eq(prod_tr, idx); eq_g = daily_eq(gated_tr, idx)
        price = raw["Close"].resample("1D").last().reindex(idx, method="ffill")
        bh = INITIAL * price / price.dropna().iloc[0]

        mp = metrics(eq_p, prod_tr); mg = metrics(eq_g, gated_tr); mb = metrics(bh)
        gate_name = "RSI gate" if GATE_CFG[sym]["rsi"] else "HMM veto"
        out["symbols"][sym] = {
            "gate": gate_name,
            "prod": mp, "gated": mg, "bh": mb,
            "prod_curve": ds(eq_p), "gated_curve": ds(eq_g), "bh_curve": ds(bh),
        }
        agg["prod"] += mp["total"]/100*INITIAL; agg["gated"] += mg["total"]/100*INITIAL

        print(f"\n  ── {sym}  ({gate_name}) ──")
        print(f"  {'':<14}{'TotalRet':>10}{'CAGR':>9}{'WR':>8}{'PF':>7}{'MaxDD':>9}{'nTrades':>9}")
        print("  "+"-"*66)
        print(f"  {'Buy & hold':<14}{mb['total']:>+9.1f}%{mb['cagr']:>+8.1f}%{'—':>8}{'—':>7}{mb['dd']:>+8.1f}%{'—':>9}")
        print(f"  {'Production':<14}{mp['total']:>+9.1f}%{mp['cagr']:>+8.1f}%{mp['wr']:>7.1f}%{mp['pf']:>7.2f}{mp['dd']:>+8.1f}%{mp['n']:>9}")
        print(f"  {'Gated':<14}{mg['total']:>+9.1f}%{mg['cagr']:>+8.1f}%{mg['wr']:>7.1f}%{mg['pf']:>7.2f}{mg['dd']:>+8.1f}%{mg['n']:>9}")

    print("\n" + "="*100)
    print(f"  PORTFOLIO PROFIT — production ${agg['prod']:+,.0f}  vs  gated ${agg['gated']:+,.0f}  "
          f"(Δ ${agg['gated']-agg['prod']:+,.0f})")
    print("="*100)
    json.dump(out, open("research/results/three_way.json","w"), indent=2, default=str)
    print("  wrote → research/results/three_way.json")


if __name__ == "__main__":
    main()
