"""
MC with realistic execution friction (Part 8.30).

Adds per-trade slippage + spread + commission to the bootstrap. Surfaces
the realistic live CAGR estimate vs the idealized backtest.

Friction model (per round trip, in basis points):
  - Pullback engine on MT5 CFDs:
      spread: 1 bp (US500 typical) -- 2 bp (XAUUSD typical)
      slippage: 2-5 bp (variable bar quality)
      commission: 0 (CFD broker; included in spread)
      overnight swap: -1 bp/night × avg hold days
  - Average estimate: 8-12 bp per round trip

We use 10 bp (= 0.10% = 0.001) as the baseline friction. User can
override via --bps flag for sensitivity tests.
"""
from __future__ import annotations
import argparse, warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY, get_pullback_cfg
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0
N_PATHS = 10_000
RNG = np.random.default_rng(42)


def trades_for(symbol):
    df = prepare_dual(load_symbol(symbol))
    cfg = get_pullback_cfg(symbol)
    bt = run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = bt["trades"].copy()
    eq = INITIAL
    rets = []
    for p in tr["pnl"]:
        rets.append(p / eq); eq += p
    tr["ret"] = rets
    tr["symbol"] = symbol
    return tr


def mc_with_friction(rets, days_realized, friction_bps, target_years=3, n_paths=N_PATHS):
    """Bootstrap with friction subtracted per trade."""
    rets = np.asarray(rets, dtype=float) - (friction_bps / 10000.0)
    n_realized = len(rets)
    trades_per_year = n_realized / max(days_realized / 365.25, 0.1)
    n_target = max(1, int(round(trades_per_year * target_years)))
    idx = RNG.integers(0, n_realized, size=(n_paths, n_target))
    sampled = np.clip(rets[idx], -0.95, None)
    eq = INITIAL * np.cumprod(1.0 + sampled, axis=1)
    final = eq[:, -1]
    rmax = np.maximum.accumulate(eq, axis=1)
    dd = ((eq - rmax) / rmax).min(axis=1)
    cagr = (final / INITIAL) ** (1.0 / target_years) - 1.0
    return {
        "final_mean": float(final.mean()),
        "final_p5":   float(np.quantile(final, 0.05)),
        "final_p50":  float(np.quantile(final, 0.50)),
        "final_p95":  float(np.quantile(final, 0.95)),
        "cagr_p5":    float(np.quantile(cagr, 0.05)),
        "cagr_p50":   float(np.quantile(cagr, 0.50)),
        "cagr_p95":   float(np.quantile(cagr, 0.95)),
        "dd_p5":      float(np.quantile(dd, 0.05)),
        "p_loss":     float((final < INITIAL).mean()),
        "p_double":   float((final > 2*INITIAL).mean()),
        "p_5x":       float((final > 5*INITIAL).mean()),
        "p_ruin":     float((final < 0.5*INITIAL).mean()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bps", type=float, default=10.0,
                   help="Friction in basis points per round trip (default 10)")
    p.add_argument("--years", type=int, default=3, help="Forward horizon (default 3)")
    args = p.parse_args()

    print(f"\n  ── MC WITH FRICTION ({args.bps} bps per trade, {args.years}yr horizon) ──")

    # Build combined returns
    per_sym = {}
    for s in SYMBOLS:
        per_sym[s] = trades_for(s)
    combined = pd.concat(per_sym.values(), ignore_index=True).sort_values("entry_time")
    eq = INITIAL; rets = []
    for p_ in combined["pnl"]:
        rets.append(p_ / eq); eq += p_
    combined["ret"] = rets
    days_r = (combined["exit_time"].max() - combined["entry_time"].min()).days

    print(f"\n  Combined portfolio (4 symbols, {len(combined)} trades over {days_r}d):")
    print(f"  {'friction':<12}{'mean $':>12}{'p5 $':>12}{'p50 $':>12}"
          f"{'p5 CAGR':>10}{'p50 CAGR':>10}{'P(2×)':>8}{'P(ruin)':>9}")
    print("  " + "-"*92)
    for f_bps in (0, 5, 10, 15, 20, 30, 50):
        m = mc_with_friction(combined["ret"].values, days_r, f_bps,
                              target_years=args.years)
        flag = " ★ baseline" if f_bps == int(args.bps) else ""
        print(f"  {f_bps:>4} bp     {m['final_mean']:>+11,.0f}"
              f"{m['final_p5']:>+11,.0f}{m['final_p50']:>+11,.0f}"
              f"{m['cagr_p50']*100:>+9.1f}%{m['cagr_p50']*100:>+9.1f}%"
              f"{m['p_double']*100:>+7.1f}%{m['p_ruin']*100:>+8.2f}%{flag}")

    print()
    print("  ── INTERPRETATION ──")
    print("  • MT5 CFD typical round-trip friction: 8-12 bp (spread + slippage)")
    print("  • Tokenized perp typical: 15-25 bp (wider spread + funding)")
    print("  • ETF on US broker: 1-3 bp (very tight)")
    print("  • The 'baseline' marker shows the user's expected friction tier")


if __name__ == "__main__":
    main()
