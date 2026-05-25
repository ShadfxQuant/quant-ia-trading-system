"""
FULL EDGE · 2.5x leverage · weak (no) gating · multi-symbol, Sharpe-weighted.

Config applied to PULLBACK for every symbol:
    pyramid_require_above_vwap        = False   (weak gating)
    pyramid_require_positive_momentum = False   (weak gating)
    max_pyramid_positions             = 10
    base_size_pct  *= 2.5   capital_cap_pct *= 2.5   (2.5x leverage)
No VIX overlay (found to be reactive drag). Sharpe-weighted aggregation, $100K start.
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pullback_exit_profile
from strategies.trend_carry import exit_profile_for as trend_carry_exit_profile
from execution.portfolio import run_portfolio, StrategySpec

UNIVERSE = ["SPY", "DIA", "QQQ"]
LEVERAGE = 2.5


def _sortino(eq, ppy=252 * 7):
    r = eq.pct_change().dropna()
    d = r[r < 0]
    return float(np.sqrt(ppy) * r.mean() / d.std()) if d.std() else 0.0


def _run_symbol(symbol):
    snap = (PULLBACK.base_size_pct, PULLBACK.capital_cap_pct,
            PULLBACK.max_pyramid_positions,
            PULLBACK.pyramid_require_above_vwap,
            PULLBACK.pyramid_require_positive_momentum,
            TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct)
    # FULL EDGE + 2.5x
    PULLBACK.pyramid_require_above_vwap = False
    PULLBACK.pyramid_require_positive_momentum = False
    PULLBACK.max_pyramid_positions = 10
    PULLBACK.base_size_pct = 0.30 * LEVERAGE
    PULLBACK.capital_cap_pct = 1.00 * LEVERAGE
    TRENDCARRY.base_size_pct = 0.12 * LEVERAGE
    TRENDCARRY.capital_cap_pct = 0.50 * LEVERAGE
    try:
        prepared = prepare_dual(load_symbol(symbol))
        strategies = [
            StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
            StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
        ]
        bt = run_portfolio(prepared, strategies, symbol=symbol)
    finally:
        (PULLBACK.base_size_pct, PULLBACK.capital_cap_pct,
         PULLBACK.max_pyramid_positions,
         PULLBACK.pyramid_require_above_vwap,
         PULLBACK.pyramid_require_positive_momentum,
         TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct) = snap

    eq = bt["equity_curve"]
    eqn = eq / eq.iloc[0]
    r = eqn.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * r.mean() / r.std()) if r.std() else 0.0
    days = (eq.index[-1] - eq.index[0]).days
    cagr = float(eqn.iloc[-1] ** (365.25 / max(days, 1)) - 1)
    dd = float(-((eqn - eqn.cummax()) / eqn.cummax()).min())
    tr = bt["trades"]
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() / -tr.loc[tr.pnl < 0, "pnl"].sum()
          if not tr.empty and (tr.pnl < 0).any() else float("inf"))
    return {"symbol": symbol, "equity_norm": eqn, "Final$": float(eq.iloc[-1]),
            "PnL$": float(eq.iloc[-1] - eq.iloc[0]), "CAGR_pct": cagr * 100,
            "DD_pct": dd * 100, "Sharpe": sharpe, "Sortino": _sortino(eq),
            "PF": float(pf), "n": int(len(tr)),
            "WR": float((tr.pnl > 0).mean()) if not tr.empty else 0.0}


def _aggregate(per, start=100_000.0):
    sh = {p["symbol"]: max(p["Sharpe"], 0.05) for p in per}
    tot = sum(sh.values())
    w = {s: v / tot for s, v in sh.items()}
    idx = sorted(set().union(*[p["equity_norm"].index for p in per]))
    a = pd.DataFrame(index=pd.DatetimeIndex(idx))
    for p in per:
        a[p["symbol"]] = p["equity_norm"].reindex(a.index).ffill().bfill()
    port = sum(a[s] * (w[s] * start) for s in a.columns)
    port.iloc[0] = start
    r = port.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * r.mean() / r.std()) if r.std() else 0.0
    days = (port.index[-1] - port.index[0]).days
    cagr = float((port.iloc[-1] / port.iloc[0]) ** (365.25 / max(days, 1)) - 1)
    dd = float(-((port - port.cummax()) / port.cummax()).min())
    return {"Final$": round(float(port.iloc[-1]), 0),
            "PnL$": round(float(port.iloc[-1] - port.iloc[0]), 0),
            "CAGR_pct": round(cagr * 100, 2), "DD_pct": round(dd * 100, 2),
            "Sharpe": round(sharpe, 3), "Sortino": round(_sortino(port), 3),
            "MAR": round(cagr / dd, 2) if dd > 0 else float("inf")}, w


def main():
    warnings.filterwarnings("ignore")
    print(f"=== FULL EDGE · {LEVERAGE}x lev · weak gating (gates OFF) · {UNIVERSE} ===\n")
    per = []
    for s in UNIVERSE:
        print(f"  {s} ...", end=" ", flush=True)
        r = _run_symbol(s)
        per.append(r)
        print(f"Final ${r['Final$']:,.0f}  PnL ${r['PnL$']:,.0f}  CAGR {r['CAGR_pct']:.1f}%  "
              f"DD {r['DD_pct']:.1f}%  PF {r['PF']:.2f}  Sharpe {r['Sharpe']:.2f}  "
              f"n={r['n']} WR={r['WR']:.0%}")
    m, w = _aggregate(per)
    print("\n=== Sharpe-weighted portfolio ($100K start) ===")
    print("  Weights: " + ", ".join(f"{s}={x:.1%}" for s, x in w.items()))
    for k, v in m.items():
        print(f"  {k:10s}: {v}")


if __name__ == "__main__":
    main()
