"""
Cross-symbol fanout validation.

Runs the production layered architecture (pullback + trend_carry) on
SPY, QQQ, and IWM independently. Then constructs an equal-weight
portfolio aggregate ($33,333 starting capital per symbol → $100k
combined) to evaluate diversification benefit.

Outputs:
    1. Per-symbol headline metrics
    2. Per-symbol per-strategy attribution
    3. Equity-curve return correlation matrix
    4. Equal-weight combined portfolio metrics
    5. Per-symbol gap analysis (WR/PF/DD outliers → Phase 4 hints)
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


def _sortino(equity: pd.Series, periods_per_year: int = 252 * 7) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    rets = equity.pct_change().dropna()
    downside = rets[rets < 0]
    if downside.std() == 0 or downside.empty:
        return 0.0
    return float(np.sqrt(periods_per_year) * rets.mean() / downside.std())


def _per_strategy(trades: pd.DataFrame) -> dict:
    rows = {}
    if trades.empty:
        return rows
    for name in trades["strategy"].unique():
        sub = trades[trades["strategy"] == name]
        wins = sub[sub["pnl"] > 0]; losses = sub[sub["pnl"] < 0]
        pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")
        rows[name] = {
            "legs": int(len(sub)),
            "entries": int(sub["entry_time"].nunique()),
            "WR": float((sub["pnl"] > 0).mean()),
            "PF": float(pf),
            "hold̄": float(sub["bars_held"].mean()),
            "$": float(sub["pnl"].sum()),
        }
    return rows


def _headline(symbol: str, trades: pd.DataFrame, equity: pd.Series, weeks: float) -> dict:
    if trades.empty:
        return {"symbol": symbol, "legs": 0, "WR": 0.0, "PF": 0.0,
                "CAGR%": 0.0, "DD%": 0.0, "Sharpe": 0.0, "MAR": 0.0,
                "tw": 0.0, "Final$": float(equity.iloc[-1]) if not equity.empty else 0.0}
    wins = trades[trades["pnl"] > 0]; losses = trades[trades["pnl"] < 0]
    pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")
    base = summarize(trades, equity)
    cagr = base["cagr"]; dd = base["max_drawdown"]
    return {
        "symbol": symbol,
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


def _run_symbol(symbol: str):
    """Run the full pipeline on a single symbol; return prepared df, trades, equity."""
    raw = load_symbol(symbol)
    prepared = prepare_dual(raw)
    strategies = [
        StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
        StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
    ]
    bt = run_portfolio(prepared, strategies, symbol=symbol)
    return prepared, bt["trades"], bt["equity_curve"]


def main() -> None:
    warnings.filterwarnings("ignore")

    headlines = []
    per_strat = {}
    equity_curves: dict[str, pd.Series] = {}
    period_str = None

    for sym in SYMBOLS:
        print(f"\n--- Running {sym} ---")
        try:
            prepared, trades, equity = _run_symbol(sym)
        except Exception as e:
            print(f"  failed: {e}")
            continue
        weeks = (prepared.index.max() - prepared.index.min()).days / 7.0
        if period_str is None:
            period_str = f"{prepared.index.min()} → {prepared.index.max()}  ({weeks:.1f} wks)"
        headlines.append(_headline(sym, trades, equity, weeks))
        per_strat[sym] = _per_strategy(trades)
        equity_curves[sym] = equity
        print(f"  done. legs={len(trades)} final=${float(equity.iloc[-1]):,.0f}")

    print(f"\n========================================================")
    print(f"  period: {period_str}")
    print(f"========================================================")

    # ---------- Per-symbol headline ----------
    print("\n=== Per-symbol headline ===")
    cols = ["symbol", "legs", "entries", "tw", "WR", "PF", "E%", "hold̄",
            "DD%", "CAGR%", "Sharpe", "Sortino", "MAR", "Final$"]
    print(pd.DataFrame(headlines)[cols].to_string(index=False))

    # ---------- Per-symbol attribution ----------
    print("\n=== Per-symbol per-strategy attribution ===")
    for sym, ps in per_strat.items():
        print(f"\n[{sym}]")
        if not ps:
            print("  no trades")
            continue
        df = pd.DataFrame.from_dict(ps, orient="index")
        df["WR"] = df["WR"].round(3)
        df["PF"] = df["PF"].round(2)
        df["hold̄"] = df["hold̄"].round(1)
        df["$"] = df["$"].round(0)
        print(df.to_string())

    # ---------- Equity curve correlations ----------
    print("\n=== Per-bar return correlation matrix ===")
    if len(equity_curves) >= 2:
        aligned = pd.concat({s: e.pct_change().fillna(0) for s, e in equity_curves.items()}, axis=1)
        # Align indices via outer join then forward-fill 0s
        aligned = aligned.fillna(0)
        corr = aligned.corr().round(3)
        print(corr.to_string())

    # ---------- Equal-weight portfolio aggregate ----------
    print("\n=== Equal-weight combined portfolio (1/N per symbol) ===")
    if equity_curves:
        # Build a unified time index
        all_index = sorted(set().union(*[e.index for e in equity_curves.values()]))
        unified = pd.DataFrame(index=pd.DatetimeIndex(all_index))
        for sym, e in equity_curves.items():
            # Convert each curve to a return-from-start series, then to portfolio fraction
            pct_curve = (e / e.iloc[0]).reindex(unified.index).ffill().bfill()
            unified[sym] = pct_curve
        # Equal weight: average the percent curves
        unified["combined"] = unified[list(equity_curves.keys())].mean(axis=1)
        combined_equity = unified["combined"] * 100_000.0   # $100k notional split 1/N
        combined_returns = combined_equity.pct_change().dropna()
        max_dd = float(-((combined_equity - combined_equity.cummax()) / combined_equity.cummax()).min())
        days = (combined_equity.index[-1] - combined_equity.index[0]).days
        cagr = float((combined_equity.iloc[-1] / combined_equity.iloc[0]) ** (365.25 / max(days, 1)) - 1)
        sharpe = float(np.sqrt(252 * 7) * combined_returns.mean() / combined_returns.std()) if combined_returns.std() else 0.0
        downside = combined_returns[combined_returns < 0]
        sortino = float(np.sqrt(252 * 7) * combined_returns.mean() / downside.std()) if downside.std() else 0.0
        mar = cagr / max_dd if max_dd > 0 else float("inf")

        print(f"  Final equity     : ${float(combined_equity.iloc[-1]):,.0f}")
        print(f"  CAGR             : {cagr * 100:.2f}%")
        print(f"  Max drawdown     : {max_dd * 100:.2f}%")
        print(f"  Sharpe           : {sharpe:.3f}")
        print(f"  Sortino          : {sortino:.3f}")
        print(f"  MAR              : {mar:.2f}")

    # ---------- Phase 4 gap hints ----------
    print("\n=== Per-symbol gap analysis (Phase 4 priorities) ===")
    if headlines:
        hdf = pd.DataFrame(headlines).set_index("symbol")
        # Find each metric's worst symbol
        for metric, direction in [("WR", "min"), ("PF", "min"), ("DD%", "max"),
                                   ("CAGR%", "min"), ("MAR", "min"), ("Sharpe", "min")]:
            worst = hdf[metric].idxmin() if direction == "min" else hdf[metric].idxmax()
            print(f"  worst {metric:6s}: {worst:<4s}  ({metric}={hdf.loc[worst, metric]})")


if __name__ == "__main__":
    main()
