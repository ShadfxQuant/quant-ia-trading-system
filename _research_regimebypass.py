"""
Regime-bypass entry test.

Runs SPY + DIA at 2.5× leverage twice:
    1. Baseline       : mom_delta > 0
    2. Regime-bypass  : (mom_delta > 0) | (RegimeScore >= threshold)

Thesis (opposite of cross-up): when RegimeScore signals expansion regime,
allow entries even on bars where micro-momentum hasn't flipped positive.
Broadens entries during institutional tailwind periods.

Sweeps thresholds 0.50 / 0.60 / 0.70 to find the sweet spot.
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
THRESHOLDS = [0.50, 0.60, 0.70]


def _sortino(eq: pd.Series, ppy: int = 252 * 7) -> float:
    r = eq.pct_change().dropna()
    d = r[r < 0]
    return float(np.sqrt(ppy) * r.mean() / d.std()) if d.std() else 0.0


def _run_symbol(symbol: str, bypass: bool, threshold: float) -> dict:
    pb_base, pb_cap = PULLBACK.base_size_pct, PULLBACK.capital_cap_pct
    tc_base, tc_cap = TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct
    pb_rb, tc_rb = PULLBACK.use_regime_bypass, TRENDCARRY.use_regime_bypass
    pb_th, tc_th = PULLBACK.regime_bypass_threshold, TRENDCARRY.regime_bypass_threshold

    PULLBACK.base_size_pct = pb_base * LEVERAGE
    PULLBACK.capital_cap_pct = pb_cap * LEVERAGE
    TRENDCARRY.base_size_pct = tc_base * LEVERAGE
    TRENDCARRY.capital_cap_pct = tc_cap * LEVERAGE
    PULLBACK.use_regime_bypass = bypass
    TRENDCARRY.use_regime_bypass = bypass
    PULLBACK.regime_bypass_threshold = threshold
    TRENDCARRY.regime_bypass_threshold = threshold

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
        PULLBACK.use_regime_bypass = pb_rb
        TRENDCARRY.use_regime_bypass = tc_rb
        PULLBACK.regime_bypass_threshold = pb_th
        TRENDCARRY.regime_bypass_threshold = tc_th

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
        return {"n": 0, "WR": 0, "PF": 0, "avg_win_pts": 0, "avg_loss_pts": 0,
                "max_win_pts": 0, "max_loss_pts": 0, "avg_hold_bars": 0,
                "max_hold_bars": 0, "winner_hold_avg": 0, "winner_hold_max": 0}
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


def _label(name: str) -> str:
    return name


def main() -> None:
    warnings.filterwarnings("ignore")

    print(f"=== Regime-bypass test · SPY+DIA · {LEVERAGE}× leverage ===\n")

    print("--- BASELINE: mom_delta > 0 ---")
    base = [_run_symbol(s, bypass=False, threshold=0.0) for s in UNIVERSE]
    for r in base:
        print(f"  {r['symbol']}: PnL=${r['PnL$']:,.0f}  DD={r['DD_pct']:.2f}%  Sharpe={r['Sharpe']:.2f}")
    base_metrics, _ = _sharpe_weighted(base)
    base_dist = _trade_distribution(
        pd.concat([r["trades"] for r in base if not r["trades"].empty], ignore_index=True)
    )

    variants = []
    for th in THRESHOLDS:
        print(f"\n--- BYPASS @ RegimeScore >= {th:.2f} ---")
        rs = [_run_symbol(s, bypass=True, threshold=th) for s in UNIVERSE]
        for r in rs:
            print(f"  {r['symbol']}: PnL=${r['PnL$']:,.0f}  DD={r['DD_pct']:.2f}%  Sharpe={r['Sharpe']:.2f}")
        m, _ = _sharpe_weighted(rs)
        d = _trade_distribution(
            pd.concat([r["trades"] for r in rs if not r["trades"].empty], ignore_index=True)
        )
        variants.append((th, m, d))

    # Portfolio aggregate comparison
    print(f"\n=== Sharpe-weighted portfolio comparison ($100K) ===")
    header = f"{'metric':<10s}  {'BASELINE':>11s}"
    for th, _, _ in variants:
        header += f"  {'BP@'+str(th):>11s}"
    print(header)
    for k in ("Final$", "PnL$", "CAGR_pct", "DD_pct", "Sharpe", "Sortino", "MAR"):
        line = f"  {k:<8s}  {base_metrics[k]:>11.2f}"
        for _, m, _ in variants:
            line += f"  {m[k]:>11.2f}"
        print(line)

    # Δ vs baseline
    print(f"\n=== Δ vs BASELINE ===")
    print(f"{'metric':<10s}  " + "  ".join(f"{'BP@'+str(th):>11s}" for th, _, _ in variants))
    for k in ("PnL$", "CAGR_pct", "DD_pct", "Sharpe", "MAR"):
        line = f"  {k:<8s}  "
        line += "  ".join(f"{(m[k]-base_metrics[k]):>+11.2f}" for _, m, _ in variants)
        print(line)

    # Trade distribution headline
    print(f"\n=== Combined trade distribution (the 'did we add good trades?' answer) ===")
    print(f"{'metric':<18s}  {'BASELINE':>10s}  " + "  ".join(f"{'BP@'+str(th):>10s}" for th, _, _ in variants))
    for k in ("n", "WR", "PF", "avg_win_pts", "max_win_pts", "avg_loss_pts",
              "avg_hold_bars", "winner_hold_avg", "winner_hold_max"):
        a = base_dist[k]
        line = f"  {k:<16s}  {a:>10.2f}"
        for _, _, d in variants:
            line += f"  {d[k]:>10.2f}"
        print(line)


if __name__ == "__main__":
    main()
