"""
Donchian breakout gate test (Part 8.31).

Per established discipline: build engine, gate on MT5 tickers, only
ship to live if it clears.

PASS criteria per symbol (3 of 4):
  - PF >= 1.5
  - CAGR >= 10%
  - DD <= 22%
  - WR >= 35% (Donchian is structurally low-WR / high-R:R)

Project gate: must clear on GC=F specifically (since that's where the
Phase 5 finding originated), AND must not REGRESS the other MT5 tickers
when compared to our pullback engine.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import pandas as pd
from config.settings import DONCHIAN
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.donchian_breakout import (
    donchian_signals, exit_profile_for as dn_exit,
)
from execution.portfolio import run_portfolio, StrategySpec

MT5_TICKERS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0


def bt(symbol):
    df = prepare_dual(load_symbol(symbol))
    df = donchian_signals(df)
    res = run_portfolio(df, [
        StrategySpec("donchian", DONCHIAN, dn_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = res["trades"]
    if len(tr) == 0: return {"symbol": symbol, "error": "no trades"}
    eq = INITIAL; peak = INITIAL; dd = 0.0
    for p in tr["pnl"]:
        eq += p; peak = max(peak, eq); dd = min(dd, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    years = max(days/365.25, 0.1)
    cagr = (eq/INITIAL)**(1/years) - 1
    wins = tr[tr["pnl"]>0]; losses = tr[tr["pnl"]<0]
    pf_w = float(wins["pnl"].sum()); pf_l = float(-losses["pnl"].sum())
    pf = pf_w / pf_l if pf_l > 0 else 999.0
    wr = float((tr["pnl"]>0).mean())
    return {"symbol": symbol, "n": len(tr), "pf": pf, "cagr": cagr,
            "dd": dd, "wr": wr, "eq": eq, "years": years}


def main():
    print("\n" + "="*100)
    print("  DONCHIAN BREAKOUT ENGINE — MT5 GATE TEST (Part 8.31)")
    print("  Compares against current production (pullback + trend_carry)")
    print("="*100)

    production = {
        "SPY":   {"pf": 3.53, "cagr": 0.200, "dd": -0.065, "wr": 0.757},
        "^NDX":  {"pf": 2.60, "cagr": 0.209, "dd": -0.066, "wr": 0.764},
        "GLD":   {"pf": 5.31, "cagr": 0.385, "dd": -0.056, "wr": 0.809},  # post per-sym
        "GC=F":  {"pf": 1.36, "cagr": 0.133, "dd": -0.155, "wr": 0.618},  # the weak one
    }

    results = []
    for s in MT5_TICKERS:
        r = bt(s)
        if "error" in r:
            print(f"  {s:<10} SKIP: {r['error']}")
            continue
        prod = production[s]
        passes = 0
        passes += int(r["pf"]   >= 1.5)
        passes += int(r["cagr"] >= 0.10)
        passes += int(abs(r["dd"]) <= 0.22)
        passes += int(r["wr"]   >= 0.35)
        delta_cagr = (r["cagr"] - prod["cagr"]) * 100
        beats = r["cagr"] > prod["cagr"] and abs(r["dd"]) <= abs(prod["dd"]) * 1.2
        verdict = "✅ BEATS prod" if beats else (
                  "PASS gate" if passes >= 3 else "❌ FAIL gate")
        print(f"  {s:<10} {verdict:<14} [{passes}/4]  "
              f"PF {r['pf']:>5.2f} (prod {prod['pf']:.2f})  "
              f"CAGR {r['cagr']*100:>+6.1f}% (Δ{delta_cagr:+.1f}pp)  "
              f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>+4.1f}%  n={r['n']}")
        results.append({"symbol": s, **r, "delta_cagr": delta_cagr, "beats": beats})

    gc_result = next((r for r in results if r["symbol"] == "GC=F"), None)
    print("\n" + "="*100)
    if gc_result and gc_result["beats"]:
        print(f"  ✅ SHIP DECISION: Donchian wins on GC=F (Δ{gc_result['delta_cagr']:+.1f}pp CAGR).")
        print(f"     RECOMMENDATION: replace pullback with Donchian on GC=F only.")
        print(f"     Per-symbol routing in main_portfolio.run() via symbol.")
    elif gc_result:
        print(f"  ❌ GC=F gate failed: only {gc_result['delta_cagr']:+.1f}pp CAGR vs production.")
        print(f"     Phase 5 finding doesn't survive a real-engine implementation. Shelve.")
    else:
        print(f"  ⚠️  GC=F backtest did not produce trades.")
    print("="*100)


if __name__ == "__main__":
    main()
