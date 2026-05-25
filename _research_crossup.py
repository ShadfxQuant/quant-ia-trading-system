"""
Cross-up early-entry test.

Runs SPY + DIA at 2.5× leverage twice:
    1. Baseline  : momentum.diff() > 0       (current production)
    2. Cross-up  : momentum cross-up only    (Δ flips from ≤0 to >0)

Reports: PnL, PF, WR, DD, avg holding bars, runner length distribution,
and biggest single win to gauge whether earlier entries produced longer
runners as predicted.
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


UNIVERSE = ["SPY", "DIA"]
LEVERAGE = 2.5


def _sortino(eq: pd.Series, ppy: int = 252 * 7) -> float:
    r = eq.pct_change().dropna()
    d = r[r < 0]
    return float(np.sqrt(ppy) * r.mean() / d.std()) if d.std() else 0.0


def _run_symbol(symbol: str, crossup: bool) -> dict:
    pb_base, pb_cap = PULLBACK.base_size_pct, PULLBACK.capital_cap_pct
    tc_base, tc_cap = TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct
    pb_cu, tc_cu = PULLBACK.use_momentum_crossup, TRENDCARRY.use_momentum_crossup

    PULLBACK.base_size_pct = pb_base * LEVERAGE
    PULLBACK.capital_cap_pct = pb_cap * LEVERAGE
    TRENDCARRY.base_size_pct = tc_base * LEVERAGE
    TRENDCARRY.capital_cap_pct = tc_cap * LEVERAGE
    PULLBACK.use_momentum_crossup = crossup
    TRENDCARRY.use_momentum_crossup = crossup

    try:
        raw = load_symbol(symbol)
        prepared = prepare_dual(raw)
        strategies = [
            StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
            StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
        ]
        bt = run_portfolio(prepared, strategies, symbol=symbol)
    finally:
        PULLBACK.base_size_pct, PULLBACK.capital_cap_pct = pb_base, pb_cap
        TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct = tc_base, tc_cap
        PULLBACK.use_momentum_crossup = pb_cu
        TRENDCARRY.use_momentum_crossup = tc_cu

    trades = bt["trades"]
    equity = bt["equity_curve"]
    eq_norm = equity / equity.iloc[0]
    rets = eq_norm.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * rets.mean() / rets.std()) if rets.std() else 0.0
    days = (equity.index[-1] - equity.index[0]).days
    cagr = float(eq_norm.iloc[-1] ** (365.25 / max(days, 1)) - 1)
    max_dd = float(-((eq_norm - eq_norm.cummax()) / eq_norm.cummax()).min())

    return {
        "symbol": symbol,
        "trades": trades,
        "equity_norm": eq_norm,
        "Final$": float(equity.iloc[-1]),
        "PnL$": float(equity.iloc[-1] - equity.iloc[0]),
        "CAGR_pct": cagr * 100,
        "DD_pct": max_dd * 100,
        "Sharpe": sharpe,
        "Sortino": _sortino(equity),
    }


def _trade_distribution(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    t = trades.copy()
    t["points"] = (t["exit_price"] - t["entry_price"]) * t["side"]
    wins = t[t["pnl"] > 0]
    losses = t[t["pnl"] < 0]
    return {
        "n": int(len(t)),
        "WR": float((t["pnl"] > 0).mean()),
        "PF": float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf"),
        "avg_win_pts": float(wins["points"].abs().mean()) if len(wins) else 0.0,
        "avg_loss_pts": float(losses["points"].abs().mean()) if len(losses) else 0.0,
        "max_win_pts": float(wins["points"].abs().max()) if len(wins) else 0.0,
        "max_loss_pts": float(losses["points"].abs().max()) if len(losses) else 0.0,
        "avg_hold_bars": float(t["bars_held"].mean()),
        "max_hold_bars": int(t["bars_held"].max()),
        "winner_hold_avg": float(wins["bars_held"].mean()) if len(wins) else 0.0,
        "winner_hold_max": int(wins["bars_held"].max()) if len(wins) else 0,
    }


def _sharpe_weighted(per_symbol: list[dict], start_capital: float = 100_000.0):
    sharpes = {p["symbol"]: max(p["Sharpe"], 0.05) for p in per_symbol}
    total = sum(sharpes.values())
    weights = {s: v / total for s, v in sharpes.items()}
    idx = sorted(set().union(*[p["equity_norm"].index for p in per_symbol]))
    aligned = pd.DataFrame(index=pd.DatetimeIndex(idx))
    for p in per_symbol:
        aligned[p["symbol"]] = p["equity_norm"].reindex(aligned.index).ffill().bfill()
    weighted = aligned.copy()
    for sym in aligned.columns:
        weighted[sym] = aligned[sym] * (weights[sym] * start_capital)
    portfolio = weighted.sum(axis=1)
    portfolio.iloc[0] = start_capital
    pnl = float(portfolio.iloc[-1] - portfolio.iloc[0])
    rets = portfolio.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * rets.mean() / rets.std()) if rets.std() else 0.0
    days = (portfolio.index[-1] - portfolio.index[0]).days
    cagr = float((portfolio.iloc[-1] / portfolio.iloc[0]) ** (365.25 / max(days, 1)) - 1)
    dd = float(-((portfolio - portfolio.cummax()) / portfolio.cummax()).min())
    return {
        "Final$": round(float(portfolio.iloc[-1]), 0),
        "PnL$": round(pnl, 0),
        "CAGR_pct": round(cagr * 100, 2),
        "DD_pct": round(dd * 100, 2),
        "Sharpe": round(sharpe, 3),
        "Sortino": round(_sortino(portfolio), 3),
        "MAR": round(cagr / dd, 2) if dd > 0 else float("inf"),
    }, weights


def main() -> None:
    warnings.filterwarnings("ignore")

    print(f"=== Cross-up early-entry test · SPY+DIA · {LEVERAGE}× leverage ===\n")

    print("--- BASELINE: momentum.diff() > 0 ---")
    base = [_run_symbol(s, crossup=False) for s in UNIVERSE]
    for r in base:
        print(f"  {r['symbol']}: PnL=${r['PnL$']:,.0f}  DD={r['DD_pct']:.2f}%  Sharpe={r['Sharpe']:.2f}")

    print("\n--- CROSS-UP: momentum cross from ≤0 to >0 ---")
    cu = [_run_symbol(s, crossup=True) for s in UNIVERSE]
    for r in cu:
        print(f"  {r['symbol']}: PnL=${r['PnL$']:,.0f}  DD={r['DD_pct']:.2f}%  Sharpe={r['Sharpe']:.2f}")

    # Portfolio aggregates
    base_metrics, base_w = _sharpe_weighted(base)
    cu_metrics, cu_w = _sharpe_weighted(cu)

    print("\n=== Sharpe-weighted portfolio comparison ($100K) ===")
    print(f"{'metric':<14s}  {'BASELINE':>12s}  {'CROSS-UP':>12s}  {'Δ':>10s}")
    for k in ("Final$", "PnL$", "CAGR_pct", "DD_pct", "Sharpe", "Sortino", "MAR"):
        a, b = base_metrics[k], cu_metrics[k]
        delta = b - a
        print(f"  {k:<12s}  {a:>12.2f}  {b:>12.2f}  {delta:>+10.2f}")

    # Trade-distribution comparison (the runner-length question)
    print("\n=== Per-symbol trade distribution (runner length focus) ===")
    cols = ["mode", "symbol", "n", "WR", "PF",
            "avg_win_pts", "max_win_pts", "avg_loss_pts",
            "avg_hold_bars", "winner_hold_avg", "winner_hold_max"]
    rows = []
    for r in base:
        d = _trade_distribution(r["trades"])
        rows.append({"mode": "baseline", "symbol": r["symbol"], **d})
    for r in cu:
        d = _trade_distribution(r["trades"])
        rows.append({"mode": "crossup", "symbol": r["symbol"], **d})

    df = pd.DataFrame(rows)
    # Round neatly
    for c in ("WR", "PF", "avg_win_pts", "max_win_pts", "avg_loss_pts",
              "avg_hold_bars", "winner_hold_avg"):
        df[c] = df[c].round(2)
    print(df[cols].to_string(index=False))

    # Combined pooled distribution (the headline "did runners get longer?" answer)
    print("\n=== Combined trades pooled (the runner-length headline) ===")
    base_all = pd.concat([r["trades"] for r in base if not r["trades"].empty], ignore_index=True)
    cu_all = pd.concat([r["trades"] for r in cu if not r["trades"].empty], ignore_index=True)
    bd = _trade_distribution(base_all)
    cd = _trade_distribution(cu_all)
    metric_names = ["n", "WR", "PF", "avg_win_pts", "max_win_pts", "avg_loss_pts",
                    "avg_hold_bars", "winner_hold_avg", "winner_hold_max"]
    print(f"{'metric':<20s}  {'BASELINE':>10s}  {'CROSS-UP':>10s}  {'Δ':>10s}")
    for k in metric_names:
        a = bd[k]; b = cd[k]
        delta = b - a if isinstance(a, (int, float)) and isinstance(b, (int, float)) else "—"
        print(f"  {k:<18s}  {a:>10.2f}  {b:>10.2f}  {delta:>+10.2f}")


if __name__ == "__main__":
    main()
