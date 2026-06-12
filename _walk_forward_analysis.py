"""
Walk-forward analysis for the production engine (Part 8.30).

The biggest validation gap: backtest shows +64% CAGR but it's all on
2024-2026 data. Is this regime-specific or persistent?

Method: split each symbol's history into 3 chronological chunks,
backtest the production engine on each chunk independently, report
per-chunk metrics. If the engine works in all 3 chunks, the edge is
persistent. If it only works in 1-2 chunks, it's regime-specific.

This is what every institutional quant calls "out-of-sample validation".
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0
N_CHUNKS = 3   # chronological splits


def bt_chunk(df_chunk, symbol, label):
    if len(df_chunk) < 200:
        return None
    res = run_portfolio(df_chunk, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = res["trades"]
    if len(tr) == 0: return {"label": label, "error": "no trades"}
    eq = INITIAL; peak = INITIAL; dd = 0.0
    for p in tr["pnl"]:
        eq += p; peak = max(peak, eq); dd = min(dd, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    years = max(days/365.25, 0.05)
    cagr = (eq/INITIAL)**(1/years) - 1
    wins = tr[tr["pnl"]>0]; losses = tr[tr["pnl"]<0]
    pf_w = float(wins["pnl"].sum()); pf_l = float(-losses["pnl"].sum())
    pf = pf_w / pf_l if pf_l > 0 else 999.0
    wr = float((tr["pnl"]>0).mean())
    return {
        "label": label, "n": len(tr), "pf": pf, "cagr": cagr,
        "dd": dd, "wr": wr, "eq": eq, "years": years,
        "start": str(df_chunk.index[0])[:10],
        "end":   str(df_chunk.index[-1])[:10],
    }


def main():
    print("\n" + "="*100)
    print("  WALK-FORWARD ANALYSIS — production engine, 3 chronological chunks per symbol")
    print("="*100)

    overall = {sym: [] for sym in SYMBOLS}

    for symbol in SYMBOLS:
        print(f"\n  ── {symbol} ──")
        df = prepare_dual(load_symbol(symbol))
        chunk_size = len(df) // N_CHUNKS
        for i in range(N_CHUNKS):
            start = i * chunk_size
            end   = (i+1) * chunk_size if i < N_CHUNKS - 1 else len(df)
            chunk = df.iloc[start:end]
            r = bt_chunk(chunk, symbol, f"chunk{i+1}")
            if r is None or "error" in r:
                print(f"    {f'chunk {i+1}':<10}  skip")
                continue
            print(f"    chunk {i+1} ({r['start']}→{r['end']}):  "
                  f"PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
                  f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%  "
                  f"n={r['n']:<4}  eq ${r['eq']:>9,.0f}")
            overall[symbol].append(r)

    # ─── Stability scoring ───
    print("\n" + "="*100)
    print("  STABILITY ASSESSMENT")
    print("="*100)
    print(f"  {'symbol':<10}{'chunks':<8}{'min PF':>9}{'mean PF':>10}"
          f"{'min CAGR':>10}{'mean CAGR':>11}{'stable?':>10}")
    print("  " + "-"*80)
    for sym, chunks in overall.items():
        if not chunks: continue
        pfs   = [c["pf"]   for c in chunks if c.get("pf") is not None]
        cagrs = [c["cagr"] for c in chunks if c.get("cagr") is not None]
        if not pfs: continue
        min_pf = min(pfs)
        mean_pf = sum(pfs) / len(pfs)
        min_cagr = min(cagrs)
        mean_cagr = sum(cagrs) / len(cagrs)
        # Stability: all chunks PF > 1.5 AND positive CAGR
        stable = all(pf > 1.5 for pf in pfs) and all(c > 0 for c in cagrs)
        flag = "✅ stable" if stable else "⚠ unstable"
        print(f"  {sym:<10}{len(chunks):<8}{min_pf:>9.2f}{mean_pf:>10.2f}"
              f"{min_cagr*100:>+9.1f}%{mean_cagr*100:>+10.1f}%  {flag}")


if __name__ == "__main__":
    main()
