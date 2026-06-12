"""
Quick exit-ladder sensitivity test on the live universe.

Smaller grid than _optimize_existing_engine.py: just stop_loss × TP1 × TP2,
keeping EMA/SMA at production defaults. Faster — ~5 min total.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
from copy import deepcopy
from itertools import product

import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "^NDX", "GLD"]
INITIAL = 100_000.0

STOP_OPTS = [0.020, 0.025, 0.030]   # 2.0% / 2.5% (current) / 3.0%
TP1_OPTS  = [0.030, 0.040, 0.050]   # 3% / 4% (current) / 5%
TP2_OPTS  = [0.100, 0.150, 0.200]   # 10% / 15% (current) / 20%


def bt_cfg(df, cfg_pb):
    res = run_portfolio(df, [
        StrategySpec("pullback", cfg_pb, pb_exit(cfg_pb)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol="X", initial_capital=INITIAL)
    tr = res["trades"]
    if len(tr) == 0: return None
    eq = INITIAL; peak = INITIAL; dd = 0.0
    for p in tr["pnl"]:
        eq += p; peak = max(peak, eq); dd = min(dd, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    years = max(days/365.25, 0.1)
    wins = tr[tr["pnl"]>0]; losses = tr[tr["pnl"]<0]
    pf_w = float(wins["pnl"].sum()); pf_l = float(-losses["pnl"].sum())
    pf = pf_w / pf_l if pf_l > 0 else 999.0
    cagr = (eq/INITIAL)**(1/years) - 1
    wr = float((tr["pnl"]>0).mean())
    return {
        "pf": pf, "cagr": cagr, "dd": dd, "wr": wr, "n": len(tr), "eq": eq,
        "score": (min(pf,10.0)-1)*25 + cagr*100 - abs(dd)*100*1.5,
    }


def main():
    print("="*100)
    print("  EXIT-LADDER SENSITIVITY SWEEP — SPY / ^NDX / GLD (production engine)")
    print(f"  Grid: stop∈{STOP_OPTS} × TP1∈{TP1_OPTS} × TP2∈{TP2_OPTS}  ({3*3*3}={27} configs per symbol)")
    print("="*100)

    for symbol in SYMBOLS:
        print(f"\n  ── {symbol} ──")
        df = prepare_dual(load_symbol(symbol))
        base = bt_cfg(df, deepcopy(PULLBACK))
        if base is None:
            print(f"    baseline failed"); continue
        print(f"    BASELINE  stop={PULLBACK.stop_loss_pct*100:.1f}% "
              f"tp1={PULLBACK.partial_tp_pct*100:.1f}% tp2={PULLBACK.final_tp_pct*100:.1f}%  "
              f"PF {base['pf']:.2f}  CAGR {base['cagr']*100:+.1f}%  "
              f"DD {base['dd']*100:+.1f}%  score {base['score']:+.1f}")

        results = []
        for stop, tp1, tp2 in product(STOP_OPTS, TP1_OPTS, TP2_OPTS):
            cfg = deepcopy(PULLBACK)
            cfg.stop_loss_pct = stop
            cfg.partial_tp_pct = tp1
            cfg.final_tp_pct = tp2
            r = bt_cfg(df, cfg)
            if r is None: continue
            r["stop"], r["tp1"], r["tp2"] = stop*100, tp1*100, tp2*100
            results.append(r)

        results.sort(key=lambda r: r["score"], reverse=True)
        print(f"    TOP 5 by score:")
        for r in results[:5]:
            d = r["score"] - base["score"]
            mark = "★" if (r["stop"], r["tp1"], r["tp2"]) == (2.5, 4.0, 15.0) else " "
            print(f"     {mark} stop={r['stop']:.1f}% tp1={r['tp1']:.1f}% tp2={r['tp2']:.1f}%  "
                  f"PF {r['pf']:.2f}  CAGR {r['cagr']*100:+.1f}%  "
                  f"DD {r['dd']*100:+.1f}%  score {r['score']:+.1f}  Δ{d:+.1f}")

        best = results[0]
        is_baseline = (best["stop"], best["tp1"], best["tp2"]) == (2.5, 4.0, 15.0)
        verdict = "✓ current config is best" if is_baseline else f"⬆️ retune candidate"
        print(f"    VERDICT: {verdict}")


if __name__ == "__main__":
    main()
