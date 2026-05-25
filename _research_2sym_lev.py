"""
2-symbol (SPY + DIA) leveraged production system + per-trade pip distribution.

Leverage model:
    2.5x leverage = each strategy's base_size_pct and capital_cap_pct scale
    by 2.5. Notional exposure can reach 2.5× equity per symbol; SHarpe is
    unchanged but PnL and DD scale ~2.5×.

Per-trade pip analysis (in price points = exit_price − entry_price × side):
    * largest win   · smallest win   · avg win   (points + $)
    * largest loss  · smallest loss  · avg loss  (points + $)
    * win/loss size ratio (the asymmetry diagnostic)
"""

from __future__ import annotations

import warnings
from copy import deepcopy
from typing import Sequence

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
USE_VIX_LEVERAGE = True   # Lever 4: dynamic VIX-conditional scaling on top of 2.5x base


def _sortino(eq: pd.Series, ppy: int = 252 * 7) -> float:
    r = eq.pct_change().dropna()
    d = r[r < 0]
    return float(np.sqrt(ppy) * r.mean() / d.std()) if d.std() else 0.0


# ---------------------------------------------------------------------------
# Single-symbol run with leverage applied
# ---------------------------------------------------------------------------

def _run_symbol_leveraged(symbol: str, leverage: float,
                          use_vix_leverage: bool = USE_VIX_LEVERAGE) -> dict:
    # Snapshot original sizes so we can restore after.
    pb_base = PULLBACK.base_size_pct
    pb_cap = PULLBACK.capital_cap_pct
    tc_base = TRENDCARRY.base_size_pct
    tc_cap = TRENDCARRY.capital_cap_pct
    pb_vix = PULLBACK.use_vix_leverage
    tc_vix = TRENDCARRY.use_vix_leverage

    PULLBACK.base_size_pct = pb_base * leverage
    PULLBACK.capital_cap_pct = pb_cap * leverage
    TRENDCARRY.base_size_pct = tc_base * leverage
    TRENDCARRY.capital_cap_pct = tc_cap * leverage
    PULLBACK.use_vix_leverage = use_vix_leverage
    TRENDCARRY.use_vix_leverage = use_vix_leverage

    try:
        raw = load_symbol(symbol)
        prepared = prepare_dual(raw)
        strategies = [
            StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
            StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
        ]
        bt = run_portfolio(prepared, strategies, symbol=symbol)
    finally:
        PULLBACK.base_size_pct = pb_base
        PULLBACK.capital_cap_pct = pb_cap
        TRENDCARRY.base_size_pct = tc_base
        TRENDCARRY.capital_cap_pct = tc_cap
        PULLBACK.use_vix_leverage = pb_vix
        TRENDCARRY.use_vix_leverage = tc_vix

    trades = bt["trades"]
    equity = bt["equity_curve"]
    eq_norm = equity / equity.iloc[0]

    rets = eq_norm.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * rets.mean() / rets.std()) if rets.std() else 0.0
    days = (equity.index[-1] - equity.index[0]).days
    cagr = float((eq_norm.iloc[-1]) ** (365.25 / max(days, 1)) - 1)
    max_dd = float(-((eq_norm - eq_norm.cummax()) / eq_norm.cummax()).min())

    return {
        "symbol": symbol,
        "trades": trades,                       # raw for downstream pip analysis
        "equity_norm": eq_norm,
        "Final$": float(equity.iloc[-1]),
        "PnL$": float(equity.iloc[-1] - equity.iloc[0]),
        "CAGR_pct": cagr * 100,
        "DD_pct": max_dd * 100,
        "Sharpe": sharpe,
        "Sortino": _sortino(equity),
    }


# ---------------------------------------------------------------------------
# Sharpe-weighted aggregation
# ---------------------------------------------------------------------------

def _aggregate_sharpe_weighted(per_symbol: list[dict],
                                start_capital: float = 100_000.0) -> tuple[dict, dict, pd.DataFrame]:
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
    sortino = _sortino(portfolio)
    days = (portfolio.index[-1] - portfolio.index[0]).days
    cagr = float((portfolio.iloc[-1] / portfolio.iloc[0]) ** (365.25 / max(days, 1)) - 1)
    max_dd = float(-((portfolio - portfolio.cummax()) / portfolio.cummax()).min())

    metrics = {
        "Final$": round(float(portfolio.iloc[-1]), 0),
        "PnL$": round(pnl, 0),
        "CAGR_pct": round(cagr * 100, 2),
        "DD_pct": round(max_dd * 100, 2),
        "Sharpe": round(sharpe, 3),
        "Sortino": round(sortino, 3),
        "MAR": round(cagr / max_dd, 2) if max_dd > 0 else float("inf"),
    }
    return metrics, weights, portfolio


# ---------------------------------------------------------------------------
# Per-trade pip analysis (price points + $)
# ---------------------------------------------------------------------------

def _pip_distribution(trades: pd.DataFrame) -> dict:
    """Return per-trade win/loss distribution in price points and dollars."""
    if trades.empty:
        return {}
    t = trades.copy()
    # Price points (1 point of price movement, signed by side).
    t["points"] = (t["exit_price"] - t["entry_price"]) * t["side"]
    t["points_pct"] = t["return_pct"] * 100   # already side-adjusted

    wins = t[t["pnl"] > 0]
    losses = t[t["pnl"] < 0]

    def _row(name, sub: pd.DataFrame):
        if sub.empty:
            return {"n": 0, "max_pts": 0.0, "min_pts": 0.0, "avg_pts": 0.0,
                    "max_$": 0.0, "min_$": 0.0, "avg_$": 0.0,
                    "max_%": 0.0, "min_%": 0.0, "avg_%": 0.0}
        return {
            "n": int(len(sub)),
            # Points
            "max_pts": float(sub["points"].abs().max()),
            "min_pts": float(sub["points"].abs().min()),
            "avg_pts": float(sub["points"].abs().mean()),
            # Dollars
            "max_$": float(sub["pnl"].abs().max()),
            "min_$": float(sub["pnl"].abs().min()),
            "avg_$": float(sub["pnl"].abs().mean()),
            # Percent
            "max_%": float(sub["points_pct"].abs().max()),
            "min_%": float(sub["points_pct"].abs().min()),
            "avg_%": float(sub["points_pct"].abs().mean()),
        }

    return {
        "wins": _row("wins", wins),
        "losses": _row("losses", losses),
        "asymmetry": (
            float(wins["pnl"].abs().mean() / losses["pnl"].abs().mean())
            if not losses.empty and not wins.empty else float("inf")
        ),
        "total_trades": int(len(t)),
        "win_rate": float((t["pnl"] > 0).mean()),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    warnings.filterwarnings("ignore")

    print(f"=== Universe: {UNIVERSE} · Base leverage: {LEVERAGE}x · "
          f"Lever 4 (VIX): {'ON' if USE_VIX_LEVERAGE else 'off'} ===\n")

    # First: run WITHOUT VIX leverage (baseline reference)
    print("--- Reference: 2.5x base, no VIX overlay ---")
    per_symbol_base = []
    for sym in UNIVERSE:
        print(f"  {sym} ...", end=" ", flush=True)
        r = _run_symbol_leveraged(sym, LEVERAGE, use_vix_leverage=False)
        per_symbol_base.append(r)
        print(f"PnL=${r['PnL$']:,.0f}  DD={r['DD_pct']:.2f}%  Sharpe={r['Sharpe']:.2f}")

    print("\n--- Final stack: 2.5x base × Lever 4 (VIX dynamic) ---")
    per_symbol = []
    for sym in UNIVERSE:
        print(f"  {sym} ...", end=" ", flush=True)
        r = _run_symbol_leveraged(sym, LEVERAGE, use_vix_leverage=USE_VIX_LEVERAGE)
        per_symbol.append(r)
        print(f"PnL=${r['PnL$']:,.0f}  DD={r['DD_pct']:.2f}%  Sharpe={r['Sharpe']:.2f}")

    # Show VIX distribution actually experienced.
    from main_portfolio import prepare_dual
    spy_prep = prepare_dual(load_symbol("SPY"))
    if "VIX" in spy_prep.columns and spy_prep["VIX"].notna().any():
        vix = spy_prep["VIX"].dropna()
        mult = spy_prep["VixLeverageMult"].dropna() if "VixLeverageMult" in spy_prep.columns else pd.Series([1.0])
        print(f"\n  VIX over period — min={vix.min():.2f}  median={vix.median():.2f}  "
              f"max={vix.max():.2f}")
        print(f"  Leverage multiplier — min={mult.min():.2f}  median={mult.median():.2f}  "
              f"max={mult.max():.2f}  mean={mult.mean():.2f}")

    # ----- Per-symbol -----
    print("\n=== Per-symbol headline ===")
    for r in per_symbol:
        print(f"  {r['symbol']}: Final ${r['Final$']:,.0f}  PnL ${r['PnL$']:,.0f}  "
              f"CAGR {r['CAGR_pct']:.2f}%  DD {r['DD_pct']:.2f}%  "
              f"Sharpe {r['Sharpe']:.2f}  Sortino {r['Sortino']:.2f}")

    # Reference baseline portfolio (no VIX overlay)
    base_metrics, base_weights, _ = _aggregate_sharpe_weighted(per_symbol_base)
    print("\n=== REFERENCE: 2.5x base only ($100K start) ===")
    print(f"  Weights: {', '.join(f'{s}={w:.1%}' for s, w in base_weights.items())}")
    for k, v in base_metrics.items():
        print(f"  {k:10s}: {v}")

    # ----- Sharpe-weighted portfolio aggregate -----
    portfolio_metrics, weights, _ = _aggregate_sharpe_weighted(per_symbol)
    print("\n=== FINAL: 2.5x base × VIX dynamic leverage ($100K start) ===")
    print(f"  Weights: {', '.join(f'{s}={w:.1%}' for s, w in weights.items())}")
    for k, v in portfolio_metrics.items():
        print(f"  {k:10s}: {v}")

    # ----- Combined trade pool for pip analysis -----
    all_trades = pd.concat([r["trades"] for r in per_symbol if not r["trades"].empty],
                            ignore_index=True) if any(not r["trades"].empty for r in per_symbol) else pd.DataFrame()

    print("\n=== Per-trade pip distribution (combined SPY+DIA, leveraged) ===")
    if all_trades.empty:
        print("  No trades.")
        return
    pd_ = _pip_distribution(all_trades)

    print(f"  Total trades: {pd_['total_trades']}   Win rate: {pd_['win_rate']:.2%}")
    print(f"  Win/Loss size ratio (avg_$_win / avg_$_loss): {pd_['asymmetry']:.2f}")

    fmt = lambda x: f"{x:,.2f}"
    for side in ("wins", "losses"):
        d = pd_[side]
        print(f"\n  --- {side.upper()} (n = {d['n']}) ---")
        print(f"    Largest  : {fmt(d['max_pts']):>10s} pts   ${fmt(d['max_$']):>10s}   {fmt(d['max_%']):>6s}%")
        print(f"    Smallest : {fmt(d['min_pts']):>10s} pts   ${fmt(d['min_$']):>10s}   {fmt(d['min_%']):>6s}%")
        print(f"    Average  : {fmt(d['avg_pts']):>10s} pts   ${fmt(d['avg_$']):>10s}   {fmt(d['avg_%']):>6s}%")

    # ----- Per-symbol pip drill-down -----
    print("\n=== Per-symbol pip drill-down ===")
    for r in per_symbol:
        if r["trades"].empty:
            continue
        pdr = _pip_distribution(r["trades"])
        print(f"\n  [{r['symbol']}]  n={pdr['total_trades']}  WR={pdr['win_rate']:.2%}  "
              f"Asymmetry={pdr['asymmetry']:.2f}")
        print(f"    WIN  : largest={pdr['wins']['max_pts']:.2f}pts (${pdr['wins']['max_$']:,.0f})  "
              f"avg={pdr['wins']['avg_pts']:.2f}pts (${pdr['wins']['avg_$']:,.0f})  "
              f"smallest={pdr['wins']['min_pts']:.2f}pts (${pdr['wins']['min_$']:,.0f})")
        print(f"    LOSS : largest={pdr['losses']['max_pts']:.2f}pts (${pdr['losses']['max_$']:,.0f})  "
              f"avg={pdr['losses']['avg_pts']:.2f}pts (${pdr['losses']['avg_$']:,.0f})  "
              f"smallest={pdr['losses']['min_pts']:.2f}pts (${pdr['losses']['min_$']:,.0f})")


if __name__ == "__main__":
    main()
