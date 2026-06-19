"""
Backtest entry gates #1 (RSI) and #2 (HMM-posterior veto) — Part 8.41.

User concern: gates must improve trade QUALITY without gutting trade COUNT.
So we report n_trades retention alongside PF/CAGR/WR/DD for:
  baseline · RSI-only · HMM-only · both · (both, looser thresholds)

Gates applied to BOTH pullback and trend_carry entry signals:
  #1 RSI:  block short if RSI < rsi_short_floor; block long if RSI > rsi_long_ceil
  #2 HMM:  block long if P_bear > p_veto; block short if P_bull > p_veto
NaN HMM posteriors (warmup) never block (NaN comparisons are False).
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

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0


def gate_signal(df, col, rsi=False, hmm=False,
                rsi_short_floor=40, rsi_long_ceil=60, p_veto=0.6):
    sig = df[col].copy()
    r = df.get("RSI_14")
    pb = df.get("P_bull"); pbr = df.get("P_bear")
    blk = pd.Series(False, index=df.index)
    if rsi and r is not None:
        blk |= (sig == -1) & (r < rsi_short_floor)
        blk |= (sig == 1) & (r > rsi_long_ceil)
    if hmm and pb is not None and pbr is not None:
        blk |= (sig == 1) & (pbr > p_veto)
        blk |= (sig == -1) & (pb > p_veto)
    sig[blk] = 0
    return sig, int((df[col] != 0).sum()), int(blk.sum())


def bt(df, sym):
    cfg = get_pullback_cfg(sym)
    res = run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=sym, initial_capital=INITIAL)
    tr = res["trades"]
    if len(tr) == 0: return None
    eq = INITIAL; peak = INITIAL; dd = 0.0
    for p in tr["pnl"]:
        eq += p; peak = max(peak, eq); dd = min(dd, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    yrs = max(days/365.25, 0.1)
    wins = tr[tr["pnl"]>0]; losses = tr[tr["pnl"]<0]
    pf = wins["pnl"].sum()/(-losses["pnl"].sum()) if len(losses) and losses["pnl"].sum()<0 else 999
    return {"n": len(tr), "wr": (tr["pnl"]>0).mean()*100, "pf": pf,
            "cagr": ((eq/INITIAL)**(1/yrs)-1)*100, "dd": dd*100, "profit": eq-INITIAL}


VARIANTS = [
    ("baseline",        dict(rsi=False, hmm=False)),
    ("RSI gate",        dict(rsi=True,  hmm=False)),
    ("HMM veto",        dict(rsi=False, hmm=True)),
    ("both",            dict(rsi=True,  hmm=True)),
    ("both (loose .7)", dict(rsi=True,  hmm=True, p_veto=0.7, rsi_short_floor=35, rsi_long_ceil=65)),
]


def main():
    print("="*108)
    print("  ENTRY-GATE BACKTEST — quality vs trade-count retention (Part 8.41)")
    print("="*108)

    totals = {v[0]: {"profit":0.0,"n":0} for v in VARIANTS}
    for sym in SYMBOLS:
        base = prepare_dual(load_symbol(sym))
        print(f"\n  ── {sym} ──")
        print(f"  {'variant':<18}{'nTrades':>9}{'retain':>8}{'WR':>7}{'PF':>7}{'CAGR':>9}{'DD':>8}{'profit':>11}{'sigBlocked':>11}")
        print("  " + "-"*88)
        base_n = None
        for name, kw in VARIANTS:
            df = base.copy()
            pb_sig, pb_total, pb_blk = gate_signal(df, "pullback_Signal", **kw)
            tc_sig, tc_total, tc_blk = gate_signal(df, "trend_carry_Signal", **kw)
            df["pullback_Signal"] = pb_sig
            df["trend_carry_Signal"] = tc_sig
            m = bt(df, sym)
            if m is None: continue
            if base_n is None: base_n = m["n"]
            retain = m["n"]/base_n*100
            blk = pb_blk + tc_blk
            print(f"  {name:<18}{m['n']:>9}{retain:>7.0f}%{m['wr']:>6.1f}%{m['pf']:>7.2f}"
                  f"{m['cagr']:>+8.1f}%{m['dd']:>+7.1f}%{m['profit']:>+11,.0f}{blk:>11}")
            totals[name]["profit"] += m["profit"]; totals[name]["n"] += m["n"]

    print("\n" + "="*108)
    print("  PORTFOLIO TOTALS (4 symbols)")
    print("="*108)
    base_profit = totals["baseline"]["profit"]; base_n = totals["baseline"]["n"]
    print(f"  {'variant':<18}{'totProfit':>12}{'vsBase':>11}{'totTrades':>11}{'retain':>9}")
    print("  " + "-"*62)
    for name, _ in VARIANTS:
        t = totals[name]
        dp = t["profit"]-base_profit; rt = t["n"]/base_n*100
        print(f"  {name:<18}{t['profit']:>+12,.0f}{dp:>+11,.0f}{t['n']:>11}{rt:>8.0f}%")


if __name__ == "__main__":
    main()
