"""
Gate test for the vol-breakout engine.

User rule (Part 8.27): build engine, gate on MT5/Infinex tickers, only
build UI/notifier infrastructure if it proves effective.

Test tickers:
  MT5:     SPY (US500), ^NDX (US100), GLD (XAUUSD), GC=F (XAUUSD-cross)
  Infinex: BTC-USD, ETH-USD

PASS criteria (per symbol, 3 of 4 metrics must clear):
  - PF >= 1.5
  - CAGR >= 8%
  - Max DD <= 22% (looser than orderflow — vol-breakout naturally has wider DD)
  - WR >= 50% (looser — vol-breakout trades fewer, bigger wins)

Project gate: >= 2 of 6 tickers pass for engine to proceed to UI/notifier build.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import pandas as pd

from config.settings import VOL_BREAKOUT
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.vol_breakout import (
    vol_breakout_signals, exit_profile_for as vb_exit,
)
from execution.portfolio import run_portfolio, StrategySpec

MT5_TICKERS     = ["SPY", "^NDX", "GLD", "GC=F"]
INFINEX_TICKERS = ["BTC-USD", "ETH-USD"]
ALL_TICKERS = MT5_TICKERS + INFINEX_TICKERS
INITIAL = 100_000.0


def bt(symbol):
    df = prepare_dual(load_symbol(symbol))
    df = vol_breakout_signals(df)
    res = run_portfolio(df, [
        StrategySpec("vol_breakout", VOL_BREAKOUT, vb_exit()),
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
        "symbol": symbol, "n": len(tr), "pf": pf, "cagr": cagr,
        "dd": dd_min, "wr": wr, "eq": eq, "years": years,
    }


def evaluate_gate(r):
    if "error" in r: return False, 0
    passes = 0
    passes += int(r["pf"]   >= 1.5)
    passes += int(r["cagr"] >= 0.08)
    passes += int(abs(r["dd"]) <= 0.22)
    passes += int(r["wr"]   >= 0.50)
    return passes >= 3, passes


def main():
    print("\n" + "="*100)
    print("  VOL-BREAKOUT ENGINE — MT5 + INFINEX GATE TEST")
    print("  Required per symbol: PF>=1.5, CAGR>=8%, DD<=22%, WR>=50% (3/4 to pass)")
    print("  Project gate: must pass on >=2 of 6 tickers to build UI/notifier")
    print("="*100)

    print("\n  MT5 universe:")
    n_mt5_pass = 0
    for s in MT5_TICKERS:
        try:
            r = bt(s)
            passes, score = evaluate_gate(r)
            if "error" in r:
                print(f"    {s:<10} SKIP — {r['error']}")
                continue
            v = "PASS" if passes else "FAIL"
            print(f"    {s:<10} {v} [{score}/4]   "
                  f"PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
                  f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%  "
                  f"n={r['n']:<4}  eq ${r['eq']:>9,.0f}  ({r['years']:.1f}y)")
            n_mt5_pass += int(passes)
        except Exception as e:
            print(f"    {s:<10} ERROR: {type(e).__name__}: {str(e)[:80]}")

    print("\n  Infinex universe:")
    n_infinex_pass = 0
    for s in INFINEX_TICKERS:
        try:
            r = bt(s)
            passes, score = evaluate_gate(r)
            if "error" in r:
                print(f"    {s:<10} SKIP — {r['error']}")
                continue
            v = "PASS" if passes else "FAIL"
            print(f"    {s:<10} {v} [{score}/4]   "
                  f"PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
                  f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%  "
                  f"n={r['n']:<4}  eq ${r['eq']:>9,.0f}  ({r['years']:.1f}y)")
            n_infinex_pass += int(passes)
        except Exception as e:
            print(f"    {s:<10} ERROR: {type(e).__name__}: {str(e)[:80]}")

    total = n_mt5_pass + n_infinex_pass
    print("\n" + "="*100)
    print(f"  GATE RESULT: {total} of {len(ALL_TICKERS)} tickers pass  "
          f"(MT5: {n_mt5_pass}/4 · Infinex: {n_infinex_pass}/2)")
    print("="*100)
    if total >= 2:
        print("  ✅ PROCEED — build dashboard page + Discord notifier")
    else:
        print("  ❌ STOP — engine does not prove effective on executable tickers")
        print("     Do NOT build UI / notifier. Either retune params or shelve.")
    return total


if __name__ == "__main__":
    main()
