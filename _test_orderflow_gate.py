"""
Gate test for the orderflow-exhaustion engine.

Per user requirement (Part 8.27): the engine must prove effective on the
MT5-aligned tickers (SPY/US500, ^NDX/US100, GLD/XAUUSD, GC=F/XAUUSD-cross)
BEFORE we build the dashboard page + Discord notifier.

PASS criteria (per symbol, must clear at least 3 of 4):
  - PF >= 1.5
  - CAGR >= 8%
  - Max DD <= 18%
  - WR >= 55%

Engine must pass on at least 2 of 4 MT5 tickers for the project to proceed
to Part 8.28 (UI + notifier integration).
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import pandas as pd

from config.settings import ORDERFLOW
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.orderflow_exhaustion import (
    orderflow_exhaustion_signals, exit_profile_for as of_exit,
)
from execution.portfolio import run_portfolio, StrategySpec

MT5_SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0


def bt(symbol):
    df = prepare_dual(load_symbol(symbol))
    df = orderflow_exhaustion_signals(df)
    res = run_portfolio(df, [
        StrategySpec("orderflow_exhaustion", ORDERFLOW, of_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = res["trades"]
    if len(tr) == 0:
        return {"symbol": symbol, "error": "no trades"}
    eq = INITIAL; peak = INITIAL; dd_min = 0.0
    for p in tr["pnl"]:
        eq += p; peak = max(peak, eq); dd_min = min(dd_min, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    years = max(days/365.25, 0.1)
    cagr = (eq/INITIAL)**(1/years) - 1
    wins = tr[tr["pnl"] > 0]; losses = tr[tr["pnl"] < 0]
    pf_w = float(wins["pnl"].sum())
    pf_l = float(-losses["pnl"].sum())
    pf = pf_w / pf_l if pf_l > 0 else float("inf")
    wr = float((tr["pnl"] > 0).mean())
    return {
        "symbol": symbol,
        "n": len(tr),
        "pf": pf,
        "cagr": cagr,
        "dd": dd_min,
        "wr": wr,
        "eq": eq,
        "years": years,
    }


def evaluate_gate(r):
    if "error" in r: return False, 0
    passes = 0
    passes += int(r["pf"]   >= 1.5)
    passes += int(r["cagr"] >= 0.08)
    passes += int(abs(r["dd"]) <= 0.18)
    passes += int(r["wr"]   >= 0.55)
    return passes >= 3, passes


def main():
    print("\n" + "="*92)
    print("  ORDERFLOW-EXHAUSTION ENGINE — MT5 GATE TEST")
    print("  Required: PF>=1.5, CAGR>=8%, DD<=18%, WR>=55%  (3/4 to pass per symbol)")
    print("  Project gate: must pass on >=2 of 4 MT5 tickers to proceed to UI/notifier")
    print("="*92)

    results = []
    n_passing = 0
    for s in MT5_SYMBOLS:
        try:
            r = bt(s)
            passes, score = evaluate_gate(r)
            r["passes"] = passes
            r["score"]  = score
            results.append(r)
            if "error" in r:
                print(f"  {s:<10} SKIP — {r['error']}")
                continue
            verdict = "PASS" if passes else "FAIL"
            print(f"  {s:<10} {verdict} [{score}/4]   "
                  f"PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
                  f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%  "
                  f"n={r['n']:<4}  eq ${r['eq']:>9,.0f}  ({r['years']:.1f}y)")
            n_passing += int(passes)
        except Exception as e:
            print(f"  {s:<10} ERROR: {type(e).__name__}: {e}")

    print("\n" + "="*92)
    print(f"  GATE RESULT: {n_passing} of {len(MT5_SYMBOLS)} MT5 symbols pass")
    print("="*92)
    if n_passing >= 2:
        print("  ✅ PROCEED — build dashboard page + Discord notifier (Part 8.28)")
    else:
        print("  ❌ STOP — engine does not prove effective on MT5 tickers")
        print("     Do NOT build UI / notifier. Either retune params or shelve.")
    return n_passing


if __name__ == "__main__":
    main()
