"""
P1-P3 validation: ATR-normalized pullback engine, cross-symbol fanout.

Step 1: Print ATR/price baseline per symbol (diagnostic — informs whether
        the chosen multipliers will produce sensible thresholds).
Step 2: Run the fanout under FIXED-% mode (current baseline).
Step 3: Run the fanout under ATR-NORMALIZED mode (P1+P2).
Step 4: Side-by-side comparison.
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
from backtest.metrics import summarize


SYMBOLS = ["SPY", "QQQ", "IWM"]


def _headline(symbol: str, mode: str, trades: pd.DataFrame, equity: pd.Series, weeks: float) -> dict:
    if trades.empty:
        return {"symbol": symbol, "mode": mode, "legs": 0, "WR": 0.0, "PF": 0.0,
                "CAGR%": 0.0, "DD%": 0.0, "MAR": 0.0, "Final$": float(equity.iloc[-1])}
    wins = trades[trades["pnl"] > 0]; losses = trades[trades["pnl"] < 0]
    pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")
    base = summarize(trades, equity)
    cagr = base["cagr"]; dd = base["max_drawdown"]
    return {
        "symbol": symbol,
        "mode": mode,
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
        "MAR": round(cagr / dd, 2) if dd > 0 else float("inf"),
        "Final$": round(float(equity.iloc[-1]), 0),
    }


def _per_strat_lines(symbol: str, mode: str, trades: pd.DataFrame) -> list[dict]:
    rows = []
    if trades.empty:
        return rows
    for name in sorted(trades["strategy"].unique()):
        sub = trades[trades["strategy"] == name]
        wins = sub[sub["pnl"] > 0]; losses = sub[sub["pnl"] < 0]
        pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")
        rows.append({
            "symbol": symbol, "mode": mode, "strategy": name,
            "legs": len(sub),
            "WR": round(float((sub["pnl"] > 0).mean()), 3),
            "PF": round(pf, 2),
            "$": round(float(sub["pnl"].sum()), 0),
        })
    return rows


def _run_symbol(symbol: str, use_atr: bool) -> tuple[dict, list[dict], float]:
    PULLBACK.use_atr_normalized = use_atr
    raw = load_symbol(symbol)
    prepared = prepare_dual(raw)
    weeks = (prepared.index.max() - prepared.index.min()).days / 7.0
    strategies = [
        StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
        StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
    ]
    bt = run_portfolio(prepared, strategies, symbol=symbol)
    mode = "ATR" if use_atr else "Fixed%"
    return (
        _headline(symbol, mode, bt["trades"], bt["equity_curve"], weeks),
        _per_strat_lines(symbol, mode, bt["trades"]),
        weeks,
    )


def main() -> None:
    warnings.filterwarnings("ignore")

    # ----- Step 1: ATR/price baseline diagnostic -----
    print("=== Step 1: ATR/Price baseline by symbol ===")
    print(f"  (informs whether the configured multipliers produce sensible thresholds)\n")
    for sym in SYMBOLS:
        raw = load_symbol(sym)
        prepared = prepare_dual(raw)
        atr_pct = (prepared["ATR"] / prepared["Close"]).dropna()
        med = float(atr_pct.median())
        print(f"  {sym}: median ATR/price = {med:.5f}  "
              f"→ implied pullback_band @ ×{PULLBACK.pullback_atr_mult} = {med * PULLBACK.pullback_atr_mult:.5f}, "
              f"stop @ ×{PULLBACK.stop_atr_mult} = {med * PULLBACK.stop_atr_mult:.5f}")
    print()

    # ----- Step 2+3: Fixed-% vs ATR-normalized -----
    print("=== Step 2: FIXED-% mode (current baseline) ===")
    fixed_headlines = []
    fixed_strats = []
    for sym in SYMBOLS:
        h, ps, _ = _run_symbol(sym, use_atr=False)
        fixed_headlines.append(h)
        fixed_strats.extend(ps)
    print(pd.DataFrame(fixed_headlines).to_string(index=False))

    print("\n=== Step 3: ATR-NORMALIZED mode (P1+P2+P3) ===")
    atr_headlines = []
    atr_strats = []
    for sym in SYMBOLS:
        h, ps, _ = _run_symbol(sym, use_atr=True)
        atr_headlines.append(h)
        atr_strats.extend(ps)
    print(pd.DataFrame(atr_headlines).to_string(index=False))

    # ----- Side by side -----
    print("\n=== Side-by-side delta (ATR − Fixed%) ===")
    fixed_df = pd.DataFrame(fixed_headlines).set_index("symbol")
    atr_df = pd.DataFrame(atr_headlines).set_index("symbol")
    delta = atr_df[["WR", "PF", "CAGR%", "DD%", "MAR", "Final$"]] - fixed_df[["WR", "PF", "CAGR%", "DD%", "MAR", "Final$"]]
    delta_with_mode = delta.round(3)
    print(delta_with_mode.to_string())

    # ----- Per-strategy attribution under ATR mode -----
    print("\n=== Per-strategy attribution (ATR mode only) ===")
    print(pd.DataFrame(atr_strats).to_string(index=False))

    # ----- Equal-weight portfolio aggregates -----
    for label, headlines in [("FIXED%", fixed_headlines), ("ATR", atr_headlines)]:
        finals = [h["Final$"] for h in headlines]
        avg_final = sum(finals) / len(finals)
        print(f"\nEqual-weight portfolio ({label}): avg final = ${avg_final:,.0f}  "
              f"(individual: {[f'${f:,.0f}' for f in finals]})")


if __name__ == "__main__":
    main()
