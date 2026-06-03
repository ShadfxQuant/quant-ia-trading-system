"""
Monte Carlo simulation on the live production config.

Method: bootstrap-resample the realized trade return stream (per-leg %
returns relative to equity at entry), reorder N times, and reconstruct an
equity curve for each path. Report distribution of CAGR, max-DD, terminal
equity, and probability of ruin / target hit.

Two modes:
  1. Per-symbol bootstrap (SPY, GLD, PAXGUSDT independently)
  2. Combined portfolio bootstrap (interleaves all trades chronologically,
     then resamples the joined return stream)

Resampling is with replacement, path length = realized N trades. This
preserves the marginal trade-return distribution but breaks temporal
autocorrelation — that's the point: it tells you how much of the realized
equity curve is path-dependent luck vs. edge.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

import numpy as np
import pandas as pd
from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

N_PATHS = 5000
INITIAL = 100_000.0
TARGET_DOUBLE = 200_000.0
RUIN_LEVEL = 50_000.0  # -50%
SYMBOLS = ["SPY", "GLD", "PAXGUSDT"]
RNG = np.random.default_rng(42)


def get_trades(symbol):
    df = prepare_dual(load_symbol(symbol))
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = bt["trades"].copy()
    # per-trade return relative to entry equity (approximation: pnl / equity_at_entry)
    # If equity_at_entry not available, use pnl/INITIAL as floor
    if "equity_at_entry" in tr.columns:
        tr["ret"] = tr["pnl"] / tr["equity_at_entry"]
    else:
        # use running equity reconstruction
        eq = INITIAL
        rets = []
        for p in tr["pnl"]:
            rets.append(p / eq)
            eq += p
        tr["ret"] = rets
    tr["symbol"] = symbol
    return tr, bt


def simulate(rets, n_paths=N_PATHS, n_trades=None, bars_per_year=None,
             realized_days=None):
    rets = np.asarray(rets, dtype=float)
    if n_trades is None:
        n_trades = len(rets)
    if n_trades == 0:
        return None
    # bootstrap matrix [n_paths, n_trades]
    idx = RNG.integers(0, len(rets), size=(n_paths, n_trades))
    sampled = rets[idx]
    # cumulative equity along each path: eq_t = INITIAL * prod(1 + r_i)
    growth = np.cumprod(1.0 + sampled, axis=1)
    equity = INITIAL * growth
    final = equity[:, -1]
    # path max-DD
    running_max = np.maximum.accumulate(equity, axis=1)
    dd = (equity - running_max) / running_max
    max_dd = dd.min(axis=1)
    # CAGR — use realized window length in years
    years = realized_days / 365.25 if realized_days else 3.0
    cagr = (final / INITIAL) ** (1.0 / years) - 1.0
    return {
        "final": final,
        "max_dd": max_dd,
        "cagr": cagr,
        "p_ruin": float((final < RUIN_LEVEL).mean()),
        "p_double": float((final > TARGET_DOUBLE).mean()),
        "p_loss": float((final < INITIAL).mean()),
    }


def fmt_pct_dist(arr, label, scale=100):
    q = np.quantile(arr, [0.05, 0.25, 0.5, 0.75, 0.95])
    mean = arr.mean() * scale
    return (f"  {label:<14} mean {mean:+6.1f}%  "
            f"p5 {q[0]*scale:+6.1f}%  p25 {q[1]*scale:+6.1f}%  "
            f"p50 {q[2]*scale:+6.1f}%  p75 {q[3]*scale:+6.1f}%  "
            f"p95 {q[4]*scale:+6.1f}%")


def fmt_dollar_dist(arr, label):
    q = np.quantile(arr, [0.05, 0.25, 0.5, 0.75, 0.95])
    mean = arr.mean()
    return (f"  {label:<14} mean ${mean:>10,.0f}  "
            f"p5 ${q[0]:>9,.0f}  p25 ${q[1]:>9,.0f}  "
            f"p50 ${q[2]:>9,.0f}  p75 ${q[3]:>9,.0f}  "
            f"p95 ${q[4]:>9,.0f}")


def report(name, res, realized_metrics=None):
    print(f"\n{'='*92}\n  MONTE CARLO — {name}  ({N_PATHS:,} paths)\n{'='*92}")
    if realized_metrics:
        print(f"  realized:  final ${realized_metrics['final']:,.0f}  ·  "
              f"CAGR {realized_metrics['cagr']*100:+.1f}%  ·  "
              f"DD {realized_metrics['dd']*100:+.1f}%  ·  "
              f"n_trades {realized_metrics['n']}")
    print()
    print(fmt_dollar_dist(res["final"], "final equity"))
    print(fmt_pct_dist(res["cagr"], "CAGR"))
    print(fmt_pct_dist(res["max_dd"], "max DD"))
    print(f"\n  P(loss money):  {res['p_loss']*100:5.1f}%")
    print(f"  P(double 2×):   {res['p_double']*100:5.1f}%")
    print(f"  P(ruin -50%):   {res['p_ruin']*100:5.1f}%")


def main():
    print("\n" + "="*92)
    print(f"  MONTE CARLO STRESS TEST — production config, {N_PATHS:,} bootstrap paths/symbol")
    print("="*92)
    print("  Resampling realized per-trade returns with replacement.")
    print("  Breaks temporal autocorrelation; preserves trade-return marginal.")

    all_trades = []
    for sym in SYMBOLS:
        try:
            tr, bt = get_trades(sym)
        except Exception as e:
            print(f"\n  [{sym}] data unavailable: {e}")
            continue
        if len(tr) == 0:
            print(f"\n  [{sym}] no trades")
            continue
        all_trades.append(tr)
        # realized window
        days = (tr["exit_time"].max() - tr["entry_time"].min()).days
        years = max(days / 365.25, 0.1)
        # reconstruct realized equity
        eq = INITIAL
        peak = INITIAL
        dd_min = 0.0
        for p in tr["pnl"]:
            eq += p
            peak = max(peak, eq)
            dd_min = min(dd_min, (eq - peak) / peak)
        realized = {
            "final": eq,
            "cagr": (eq / INITIAL) ** (1.0 / years) - 1.0,
            "dd": dd_min,
            "n": len(tr),
        }
        res = simulate(tr["ret"].values, realized_days=days)
        report(sym, res, realized)

    # Combined
    if all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
        combined = combined.sort_values("entry_time").reset_index(drop=True)
        days = (combined["exit_time"].max() - combined["entry_time"].min()).days
        years = max(days / 365.25, 0.1)
        # realized combined (treat as serial equity — each trade scales the
        # running portfolio equity; this is an idealization).
        eq = INITIAL; peak = INITIAL; dd_min = 0.0
        for r in combined["ret"]:
            eq *= (1.0 + r)
            peak = max(peak, eq)
            dd_min = min(dd_min, (eq - peak) / peak)
        realized = {
            "final": eq,
            "cagr": (eq / INITIAL) ** (1.0 / years) - 1.0,
            "dd": dd_min,
            "n": len(combined),
        }
        res = simulate(combined["ret"].values, realized_days=days)
        report("COMBINED (SPY+GLD+PAXG, serial)", res, realized)


if __name__ == "__main__":
    main()
