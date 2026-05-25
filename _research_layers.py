"""
Layered-architecture comparison matrix.

Runs the four configurations:
    BASELINE   : core alpha engine alone (Phase 2 winner)
    +L2        : adaptive entry sensitivity (Layer 2)
    +L3        : trend carry sleeve only (Layer 3, no adaptive entry)
    +L2+L3     : full layered system (Layers 2 + 3 + 4 regime multiplier)

Each configuration is run on the same prepared dataframe so the only
variation is which layers are active.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pullback_exit_profile
from strategies.trend_carry import exit_profile_for as trend_carry_exit_profile
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import summarize


def _sortino(equity: pd.Series, periods_per_year: int = 252 * 7) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    rets = equity.pct_change().dropna()
    downside = rets[rets < 0]
    if downside.std() == 0 or downside.empty:
        return 0.0
    return float(np.sqrt(periods_per_year) * rets.mean() / downside.std())


def _summary(name: str, trades: pd.DataFrame, equity: pd.Series, weeks: float) -> dict:
    if trades.empty:
        return {"name": name, "legs": 0, "PF": 0.0, "WR": 0.0, "CAGR%": 0.0,
                "DD%": 0.0, "Sharpe": 0.0, "Sortino": 0.0, "MAR": 0.0,
                "tw": 0.0, "hold̄": 0.0, "Final$": float(equity.iloc[-1])}
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] < 0]
    pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")
    base = summarize(trades, equity)
    cagr = base["cagr"]
    dd = base["max_drawdown"]
    return {
        "name": name,
        "legs": int(len(trades)),
        "entries": int(trades["entry_time"].nunique()),
        "tw": round(trades["entry_time"].nunique() / weeks, 3),
        "WR": round(float((trades["pnl"] > 0).mean()), 3),
        "PF": round(pf, 2),
        "E%": round(float(trades["return_pct"].mean()) * 100, 2),
        "hold̄": round(float(trades["bars_held"].mean()), 1),
        "DD%": round(dd * 100, 2),
        "CAGR%": round(cagr * 100, 2),
        "Sharpe": round(base["sharpe"], 3),
        "Sortino": round(_sortino(equity), 3),
        "MAR": round(cagr / dd, 2) if dd > 0 else float("inf"),
        "Final$": round(float(equity.iloc[-1]), 0),
    }


def _per_strategy(trades: pd.DataFrame, strategies, weeks: float) -> list[dict]:
    rows = []
    for s in strategies:
        sub = trades[trades["strategy"] == s.name] if not trades.empty else trades
        if sub.empty:
            rows.append({"strat": s.name, "legs": 0, "WR": 0, "PF": 0, "$": 0})
            continue
        wins = sub[sub["pnl"] > 0]; losses = sub[sub["pnl"] < 0]
        pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")
        rows.append({
            "strat": s.name,
            "legs": len(sub),
            "entries": int(sub["entry_time"].nunique()),
            "tw": round(sub["entry_time"].nunique() / weeks, 3),
            "WR": round(float((sub["pnl"] > 0).mean()), 3),
            "PF": round(pf, 2),
            "hold̄": round(float(sub["bars_held"].mean()), 1),
            "$": round(float(sub["pnl"].sum()), 0),
        })
    return rows


def _run_one(prepared: pd.DataFrame, name: str, use_l2: bool, use_l3: bool,
             weeks: float) -> tuple[dict, list[dict]]:
    # Toggle Layer 2 — adaptive entry — by flipping the flag and regenerating
    # pullback signals on the prepared frame (idempotent).
    from strategies.pullback import generate_signals as pullback_signals
    original_use = PULLBACK.use_adaptive_entry
    PULLBACK.use_adaptive_entry = use_l2
    try:
        df = pullback_signals(prepared.copy(deep=True))

        strategies = [
            StrategySpec(name="pullback", cfg=PULLBACK, exit_profile=pullback_exit_profile()),
        ]
        if use_l3:
            strategies.append(
                StrategySpec(name="trend_carry", cfg=TRENDCARRY,
                             exit_profile=trend_carry_exit_profile()),
            )
        bt = run_portfolio(df, strategies, symbol="SPY")
    finally:
        PULLBACK.use_adaptive_entry = original_use

    return _summary(name, bt["trades"], bt["equity_curve"], weeks), \
           _per_strategy(bt["trades"], strategies, weeks)


def main() -> None:
    raw = load_symbol("SPY")
    prepared = prepare_dual(raw)
    weeks = (prepared.index.max() - prepared.index.min()).days / 7.0
    print(f"period: {prepared.index.min()} → {prepared.index.max()}  ({weeks:.1f} wks)")

    # RegimeScore distribution summary
    rs = prepared["RegimeScore"].dropna()
    expansion_pct = float((rs >= 0.6).mean())
    chop_pct = float((rs <= 0.4).mean())
    print(f"RegimeScore — mean={rs.mean():.3f}  expansion≥0.6: {expansion_pct:.1%}  chop≤0.4: {chop_pct:.1%}\n")

    variants = [
        ("BASELINE",   False, False),
        ("+L2_adapt",  True,  False),
        ("+L3_carry",  False, True),
        ("+L2+L3",     True,  True),
    ]

    print("=== Portfolio matrix ===")
    rows = []
    per_strat_dump = {}
    for name, use_l2, use_l3 in variants:
        summary, ps = _run_one(prepared, name, use_l2, use_l3, weeks)
        rows.append(summary)
        per_strat_dump[name] = ps
    cols = ["name", "legs", "entries", "tw", "WR", "PF", "E%", "hold̄",
            "DD%", "CAGR%", "Sharpe", "Sortino", "MAR", "Final$"]
    print(pd.DataFrame(rows)[cols].to_string(index=False))

    print("\n=== Per-strategy attribution ===")
    for name, ps in per_strat_dump.items():
        print(f"\n[{name}]")
        if ps:
            print(pd.DataFrame(ps).to_string(index=False))


if __name__ == "__main__":
    main()
