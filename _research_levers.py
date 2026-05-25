"""
Lever-stack progression: equal-weight → Sharpe-weighted → +universe → +vol target.

Builds a clean before/after matrix showing the cumulative PnL impact of
each institutional lever applied on top of the ATR-normalized production
architecture. No new strategy modules — purely sizing/allocation overlays.

Steps:
    1. Equal-weight baseline (3 symbols: SPY/QQQ/IWM)
    2. + Sharpe-weighted allocation  (Lever 1)
    3. + Universe expansion to 6 symbols (Lever 2)
    4. + Volatility targeting (Lever 3)

All runs use the production config: ATR-normalized thresholds, TP15 +
NoTrail + BE exit ladder, pullback + trend_carry strategies in parallel.
"""

from __future__ import annotations

import warnings
from typing import Sequence

import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pullback_exit_profile
from strategies.trend_carry import exit_profile_for as trend_carry_exit_profile
from execution.portfolio import run_portfolio, StrategySpec


CORE_3 = ["SPY", "QQQ", "IWM"]
EXPANDED_6 = ["SPY", "QQQ", "IWM", "XLK", "XLF", "DIA"]


# ---------------------------------------------------------------------------
# Per-symbol single-run + metrics
# ---------------------------------------------------------------------------

def _sortino(eq: pd.Series, ppy: int = 252 * 7) -> float:
    if eq.empty or len(eq) < 2:
        return 0.0
    r = eq.pct_change().dropna()
    d = r[r < 0]
    if d.std() == 0 or d.empty:
        return 0.0
    return float(np.sqrt(ppy) * r.mean() / d.std())


def _run_symbol(symbol: str, vol_target: bool) -> dict:
    """Run pullback + trend_carry on a single symbol with toggleable vol targeting."""
    PULLBACK.use_vol_targeting = vol_target
    TRENDCARRY.use_vol_targeting = vol_target
    raw = load_symbol(symbol)
    prepared = prepare_dual(raw)
    strategies = [
        StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
        StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
    ]
    bt = run_portfolio(prepared, strategies, symbol=symbol)
    trades = bt["trades"]
    equity = bt["equity_curve"]

    if trades.empty:
        return {"symbol": symbol, "PF": 0.0, "WR": 0.0, "Sharpe": 0.0,
                "CAGR_pct": 0.0, "DD_pct": 0.0, "Final$": float(equity.iloc[-1]),
                "PnL$": 0.0, "equity": equity}

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] < 0]
    pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")

    eq_norm = equity / equity.iloc[0]
    max_dd = float(-((eq_norm - eq_norm.cummax()) / eq_norm.cummax()).min())
    rets = eq_norm.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * rets.mean() / rets.std()) if rets.std() else 0.0
    days = (equity.index[-1] - equity.index[0]).days
    cagr = float((eq_norm.iloc[-1]) ** (365.25 / max(days, 1)) - 1)

    return {
        "symbol": symbol,
        "PF": round(pf, 2),
        "WR": round(float((trades["pnl"] > 0).mean()), 3),
        "Sharpe": round(sharpe, 3),
        "Sortino": round(_sortino(equity), 3),
        "CAGR_pct": round(cagr * 100, 2),
        "DD_pct": round(max_dd * 100, 2),
        "PnL$": round(float(equity.iloc[-1] - equity.iloc[0]), 0),
        "Final$": round(float(equity.iloc[-1]), 0),
        "equity_norm": eq_norm,
    }


# ---------------------------------------------------------------------------
# Aggregation under different weighting schemes
# ---------------------------------------------------------------------------

def _aggregate(per_symbol: list[dict], weights: dict[str, float],
               start_capital: float = 100_000.0) -> dict:
    """Build a weighted aggregate equity curve and compute portfolio metrics.

    `weights[symbol]` are normalised fractions summing to 1.
    Each symbol's normalised equity curve is scaled by `weights[sym] *
    start_capital`, then summed to get the combined portfolio equity.
    """
    # Build a union time index across all symbols.
    all_idx = sorted(set().union(*[p["equity_norm"].index for p in per_symbol]))
    aligned = pd.DataFrame(index=pd.DatetimeIndex(all_idx))
    for p in per_symbol:
        aligned[p["symbol"]] = p["equity_norm"].reindex(aligned.index).ffill().bfill()
    # Weighted contribution: each symbol's dollars = weight × start × eq_norm
    weighted = aligned.copy()
    for sym in aligned.columns:
        w = weights.get(sym, 0.0)
        weighted[sym] = aligned[sym] * (w * start_capital)
    portfolio_eq = weighted.sum(axis=1)
    portfolio_eq.iloc[0] = start_capital  # exact starting value

    pnl = float(portfolio_eq.iloc[-1] - portfolio_eq.iloc[0])
    rets = portfolio_eq.pct_change().dropna()
    sharpe = float(np.sqrt(252 * 7) * rets.mean() / rets.std()) if rets.std() else 0.0
    sortino = _sortino(portfolio_eq)
    days = (portfolio_eq.index[-1] - portfolio_eq.index[0]).days
    cagr = float((portfolio_eq.iloc[-1] / portfolio_eq.iloc[0]) ** (365.25 / max(days, 1)) - 1)
    max_dd = float(-((portfolio_eq - portfolio_eq.cummax()) / portfolio_eq.cummax()).min())
    return {
        "Final$": round(float(portfolio_eq.iloc[-1]), 0),
        "PnL$": round(pnl, 0),
        "CAGR_pct": round(cagr * 100, 2),
        "DD_pct": round(max_dd * 100, 2),
        "Sharpe": round(sharpe, 3),
        "Sortino": round(sortino, 3),
        "MAR": round(cagr / max_dd, 2) if max_dd > 0 else float("inf"),
    }


def _equal_weights(symbols: Sequence[str]) -> dict[str, float]:
    n = len(symbols)
    return {s: 1.0 / n for s in symbols}


def _sharpe_weights(per_symbol: list[dict], floor: float = 0.05) -> dict[str, float]:
    """Weights proportional to Sharpe (clipped to ≥ floor before normalising).

    Note: this uses full-period Sharpe, which is lookahead-biased. For a
    live system you'd use a rolling Sharpe computed on a training window.
    """
    raw = {p["symbol"]: max(float(p["Sharpe"]), floor) for p in per_symbol}
    total = sum(raw.values())
    return {s: v / total for s, v in raw.items()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    warnings.filterwarnings("ignore")

    # ----- Run each symbol once per (universe × vol-targeting) cell -----
    print("=== Running symbols (universe × vol-targeting cells) ===")
    results = {}
    for vt in [False, True]:
        for sym in EXPANDED_6:
            key = (sym, vt)
            print(f"  {sym}  vol_target={vt} ...", end=" ", flush=True)
            try:
                r = _run_symbol(sym, vol_target=vt)
                results[key] = r
                print(f"PF={r['PF']}  Sharpe={r['Sharpe']}  PnL=${r['PnL$']:,.0f}")
            except Exception as e:
                print(f"FAILED — {e}")

    # Helper to pull a slice of per-symbol results.
    def slice_results(symbols, vt):
        return [results[(s, vt)] for s in symbols if (s, vt) in results]

    # ----- Build the four configurations -----
    print("\n========================================================")
    print("=== Lever-stack progression matrix ($100K capital) ===")
    print("========================================================")
    rows = []

    # Configuration 0: equal-weight 3-symbol, no vol target (current production)
    base = slice_results(CORE_3, False)
    rows.append({"config": "0_baseline_eq3", "n_symbols": len(base),
                 **_aggregate(base, _equal_weights(CORE_3))})

    # Configuration 1: Sharpe-weighted 3-symbol (Lever 1)
    rows.append({"config": "1_sharpeW_eq3", "n_symbols": len(base),
                 **_aggregate(base, _sharpe_weights(base))})

    # Configuration 2: Sharpe-weighted 6-symbol (Lever 1 + Lever 2)
    expanded = slice_results(EXPANDED_6, False)
    rows.append({"config": "2_sharpeW_6sym", "n_symbols": len(expanded),
                 **_aggregate(expanded, _sharpe_weights(expanded))})

    # Configuration 3: Sharpe-weighted 6-symbol + vol targeting (Lever 1 + 2 + 3)
    expanded_vt = slice_results(EXPANDED_6, True)
    rows.append({"config": "3_full_stack", "n_symbols": len(expanded_vt),
                 **_aggregate(expanded_vt, _sharpe_weights(expanded_vt))})

    df = pd.DataFrame(rows)
    cols = ["config", "n_symbols", "Final$", "PnL$", "CAGR_pct", "DD_pct",
            "Sharpe", "Sortino", "MAR"]
    print(df[cols].to_string(index=False))

    # ----- Sharpe weight breakdown -----
    print("\n=== Sharpe-weighted allocation (full universe, no vol target) ===")
    sw = _sharpe_weights(expanded)
    for sym, w in sorted(sw.items(), key=lambda x: -x[1]):
        sharpe = next(p["Sharpe"] for p in expanded if p["symbol"] == sym)
        pnl = next(p["PnL$"] for p in expanded if p["symbol"] == sym)
        print(f"  {sym:5s}  weight={w:.1%}  (Sharpe={sharpe:.2f}, per-symbol PnL on $100K=${pnl:,.0f})")

    # ----- Per-symbol contribution to the final-stack PnL -----
    print("\n=== Per-symbol PnL with vol targeting (Lever 3 effect) ===")
    sym_rows = []
    for p_off, p_on in zip(slice_results(EXPANDED_6, False),
                           slice_results(EXPANDED_6, True)):
        sym_rows.append({
            "symbol": p_off["symbol"],
            "PnL$_off": p_off["PnL$"],
            "PnL$_on": p_on["PnL$"],
            "Δ$": p_on["PnL$"] - p_off["PnL$"],
            "PF_off": p_off["PF"],
            "PF_on": p_on["PF"],
            "DD%_off": p_off["DD_pct"],
            "DD%_on": p_on["DD_pct"],
        })
    print(pd.DataFrame(sym_rows).to_string(index=False))


if __name__ == "__main__":
    main()
