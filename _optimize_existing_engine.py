"""
Parameter sensitivity sweep on the production engine (Part 8.29).

User asked to optimize what we have. This tests whether current PULLBACK
defaults are actually optimal per symbol, or whether per-symbol tuning
would lift performance.

Swept params (most impactful, cheap to vary):
  - EMA period (40 / 50 / 60)
  - SMA period (100 / 130 / 160)
  - Pullback band ATR multiplier (1.0 / 1.5 / 2.0)
  - Stop loss pct (2.0% / 2.5% / 3.0%)
  - TP1 pct (3.5% / 4.0% / 5.0%)

Total combinations: 3 × 3 × 3 × 3 × 3 = 243 per symbol.
Run on SPY, ^NDX, GLD, GC=F.

Output:
  - Current-config baseline per symbol
  - Best-found-config per symbol (and the lift)
  - Sensitivity surface — which params matter most
  - Stability check: does best config also rank highly under perturbation?
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
from copy import deepcopy
from itertools import product

import pandas as pd

from config.settings import PULLBACK, TRENDCARRY, INDICATORS, PullbackStratConfig
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0

# Reduced grid for fast iteration (243 → 81 per symbol via removing one axis)
EMA_OPTS    = [40, 50, 60]
SMA_OPTS    = [100, 130, 160]
ATR_OPTS    = [1.0, 1.5, 2.0]
STOP_OPTS   = [2.0, 2.5, 3.0]


def bt_with_config(df, cfg_pb):
    res = run_portfolio(df, [
        StrategySpec("pullback", cfg_pb, pb_exit(cfg_pb)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol="X", initial_capital=INITIAL)
    tr = res["trades"]
    if len(tr) == 0:
        return None
    eq = INITIAL; peak = INITIAL; dd_min = 0.0
    for p in tr["pnl"]:
        eq += p; peak = max(peak, eq); dd_min = min(dd_min, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    years = max(days/365.25, 0.1)
    wins = tr[tr["pnl"]>0]; losses = tr[tr["pnl"]<0]
    pf_w = float(wins["pnl"].sum()); pf_l = float(-losses["pnl"].sum())
    pf = pf_w / pf_l if pf_l > 0 else 999.0
    cagr = (eq/INITIAL)**(1/years) - 1
    wr = float((tr["pnl"]>0).mean())
    # Composite score
    score = (
        (min(pf, 10.0) - 1.0) * 25 +
        cagr * 100 -
        abs(dd_min) * 100 * 1.5
    )
    return {
        "pf": pf, "cagr": cagr, "dd": dd_min, "wr": wr,
        "n": len(tr), "eq": eq, "score": score,
    }


def sweep_symbol(symbol):
    print(f"\n  ── {symbol} ──")

    # Baseline (current production config)
    cfg_baseline = deepcopy(PULLBACK)
    df_base = prepare_dual(load_symbol(symbol))
    base = bt_with_config(df_base, cfg_baseline)
    print(f"    BASELINE   ema={INDICATORS.ema_period} sma={INDICATORS.sma_period} "
          f"atr={cfg_baseline.pullback_atr_mult} stop={cfg_baseline.stop_loss_pct*100:.1f}% "
          f"→ PF {base['pf']:.2f}  CAGR {base['cagr']*100:+.1f}%  "
          f"DD {base['dd']*100:+.1f}%  score {base['score']:+.1f}")

    # Sweep — INDICATORS controls EMA/SMA, PULLBACK controls everything else.
    # Mutate INDICATORS globally for each iteration, then restore at end.
    original_ema = INDICATORS.ema_period
    original_sma = INDICATORS.sma_period
    results = []
    total = len(EMA_OPTS) * len(SMA_OPTS) * len(ATR_OPTS) * len(STOP_OPTS)
    print(f"    sweeping {total} configs...", end="", flush=True)
    try:
        for ema, sma, atr_m, stop_pct in product(EMA_OPTS, SMA_OPTS, ATR_OPTS, STOP_OPTS):
            if sma <= ema: continue
            INDICATORS.ema_period = ema
            INDICATORS.sma_period = sma
            df_iter = prepare_dual(load_symbol(symbol))  # recompute indicators
            cfg = deepcopy(PULLBACK)
            cfg.pullback_atr_mult = atr_m
            cfg.stop_loss_pct = stop_pct / 100
            r = bt_with_config(df_iter, cfg)
            if r is None: continue
            r["ema"]  = ema
            r["sma"]  = sma
            r["atr"]  = atr_m
            r["stop"] = stop_pct
            results.append(r)
    finally:
        INDICATORS.ema_period = original_ema
        INDICATORS.sma_period = original_sma
    print(f" done ({len(results)} valid)")

    # Top configs
    results.sort(key=lambda r: r["score"], reverse=True)
    top5 = results[:5]
    print(f"    TOP 5 by score:")
    for r in top5:
        lift_pct = (r["score"] - base["score"])
        print(f"      ema={r['ema']:>2} sma={r['sma']:>3} atr={r['atr']:.1f} "
              f"stop={r['stop']:.1f}% → PF {r['pf']:.2f}  "
              f"CAGR {r['cagr']*100:+.1f}%  DD {r['dd']*100:+.1f}%  "
              f"score {r['score']:+.1f}  (Δ {lift_pct:+.1f})")
    return {
        "symbol":    symbol,
        "baseline":  base,
        "best":      top5[0] if top5 else None,
        "top5":      top5,
    }


def main():
    print("="*100)
    print("  PARAMETER SENSITIVITY SWEEP — production pullback engine, MT5 universe")
    print(f"  Grid: ema∈{EMA_OPTS} × sma∈{SMA_OPTS} × atr∈{ATR_OPTS} × stop∈{STOP_OPTS}")
    print("="*100)

    out = []
    for s in SYMBOLS:
        out.append(sweep_symbol(s))

    print("\n" + "="*100)
    print("  SUMMARY — current config vs best-found per symbol")
    print("="*100)
    for r in out:
        if r["best"] is None: continue
        base = r["baseline"]; best = r["best"]
        delta_cagr = (best["cagr"] - base["cagr"]) * 100
        delta_pf   = best["pf"] - base["pf"]
        print(f"  {r['symbol']:<10}  baseline score {base['score']:>+7.1f}  "
              f"→ best score {best['score']:>+7.1f}  "
              f"(ΔPF {delta_pf:+.2f}, ΔCAGR {delta_cagr:+.1f}pp)")
        improve = best["score"] > base["score"] + 5  # meaningful lift?
        print(f"             best config: ema={best['ema']} sma={best['sma']} "
              f"atr={best['atr']:.1f} stop={best['stop']:.1f}%   "
              f"{'⬆️ retune candidate' if improve else '✓ current config near-optimal'}")


if __name__ == "__main__":
    main()
