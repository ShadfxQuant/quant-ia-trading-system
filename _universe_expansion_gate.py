"""
Universe expansion gate test (Part 8.29).

User asked to expand pool beyond indices — stocks and crypto. Per established
gate-first discipline: backtest each candidate through the production engine
(pullback + trend_carry + Kalman + regime-flip), apply quality gate, surface
promotion-ready symbols only.

Candidates:
  Mega-cap stocks (might be on Infinex tokenized, or any equity broker):
      MSFT, META, GOOGL, AMZN, AAPL, NVDA, AVGO, TSLA, ORCL, JPM, V, WMT
  Crypto (Infinex universe):
      BTC-USD, ETH-USD, SOL-USD, BNB-USD, AVAX-USD, ADA-USD, MATIC-USD, DOT-USD

Promotion gate (per symbol, must clear 3 of 4):
  - PF >= 2.0
  - CAGR >= 10%
  - DD <= 20%
  - n_trades >= 50

Output:
  - per-symbol metrics table
  - promotion-ready leaderboard
  - watchlist tier (cleared 2/4 — worth monitoring)
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

INITIAL = 100_000.0

STOCKS = ["MSFT", "META", "GOOGL", "AMZN", "AAPL", "NVDA",
          "AVGO", "TSLA", "ORCL", "JPM", "V", "WMT"]
CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD",
          "AVAX-USD", "ADA-USD", "MATIC-USD", "DOT-USD"]

def bt(symbol):
    try:
        df = prepare_dual(load_symbol(symbol))
        res = run_portfolio(df, [
            StrategySpec("pullback", PULLBACK, pb_exit()),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=symbol, initial_capital=INITIAL)
        tr = res["trades"]
        if len(tr) == 0:
            return {"symbol": symbol, "error": "no trades"}
        eq = INITIAL; peak = INITIAL; dd_min = 0.0
        for p in tr["pnl"]:
            eq += p; peak = max(peak, eq); dd_min = min(dd_min, (eq-peak)/peak)
        days = (tr["exit_time"].max() - tr["entry_time"].min()).days
        years = max(days/365.25, 0.1)
        wins = tr[tr["pnl"]>0]; losses = tr[tr["pnl"]<0]
        pf_w = float(wins["pnl"].sum()); pf_l = float(-losses["pnl"].sum())
        pf = pf_w / pf_l if pf_l > 0 else float("inf")
        cagr = (eq/INITIAL)**(1/years) - 1
        wr = float((tr["pnl"]>0).mean())
        return {
            "symbol": symbol, "n": len(tr), "pf": pf, "cagr": cagr,
            "dd": dd_min, "wr": wr, "eq": eq, "years": years,
        }
    except Exception as e:
        return {"symbol": symbol, "error": f"{type(e).__name__}: {str(e)[:60]}"}


def gate(r):
    if "error" in r: return None
    score = 0
    score += int(r["pf"]   >= 2.0)
    score += int(r["cagr"] >= 0.10)
    score += int(abs(r["dd"]) <= 0.20)
    score += int(r["n"]    >= 50)
    return score


def main():
    print("\n" + "="*100)
    print("  UNIVERSE EXPANSION GATE — Part 8.29")
    print("  Promotion gate per symbol: PF>=2.0 AND CAGR>=10% AND DD<=20% AND n>=50 (3 of 4)")
    print("="*100)

    all_results = []
    for label, syms in [("MEGA-CAP STOCKS", STOCKS), ("CRYPTO", CRYPTO)]:
        print(f"\n  ── {label} ──")
        for s in syms:
            r = bt(s)
            score = gate(r)
            if "error" in r:
                print(f"    {s:<10} SKIP — {r['error']}")
                continue
            r["score"] = score
            all_results.append(r)
            verdict = "✅ PROMO" if score >= 3 else "👀 watch" if score == 2 else "❌ pass"
            print(f"    {s:<10} {verdict} [{score}/4]  "
                  f"PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
                  f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%  "
                  f"n={r['n']:<4}  eq ${r['eq']:>9,.0f}")

    # Sort by composite score
    promo = [r for r in all_results if r["score"] >= 3]
    watch = [r for r in all_results if r["score"] == 2]

    print("\n" + "="*100)
    print(f"  PROMOTION-READY ({len(promo)})  — ready to add to DATA.symbols")
    print("="*100)
    promo.sort(key=lambda r: r["pf"], reverse=True)
    for r in promo:
        print(f"  {r['symbol']:<10} PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
              f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%  "
              f"profit ${r['eq']-INITIAL:>+10,.0f}")

    print("\n" + "="*100)
    print(f"  WATCHLIST ({len(watch)}) — 2/4 cleared, worth monitoring")
    print("="*100)
    watch.sort(key=lambda r: r["pf"], reverse=True)
    for r in watch:
        print(f"  {r['symbol']:<10} PF {r['pf']:>5.2f}  CAGR {r['cagr']*100:>+6.1f}%  "
              f"DD {r['dd']*100:>+6.1f}%  WR {r['wr']*100:>4.1f}%")


if __name__ == "__main__":
    main()
