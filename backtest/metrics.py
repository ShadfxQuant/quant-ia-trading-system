"""
Performance metrics for the backtester.

All inputs assume a `trades` DataFrame with at least the columns:
    entry_time, exit_time, side, entry_price, exit_price, pnl, return_pct
and an `equity_curve` Series indexed by datetime.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def win_rate(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    return float((trades["pnl"] > 0).mean())


def profit_factor(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    gross_profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    gross_loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def max_drawdown(equity_curve: pd.Series) -> float:
    """Returns the worst peak-to-trough drawdown as a positive fraction."""
    if equity_curve.empty:
        return 0.0
    running_peak = equity_curve.cummax()
    drawdown = (equity_curve - running_peak) / running_peak
    return float(-drawdown.min())


def cagr(equity_curve: pd.Series) -> float:
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0
    start, end = equity_curve.iloc[0], equity_curve.iloc[-1]
    if start <= 0:
        return 0.0
    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    return float((end / start) ** (1 / years) - 1)


def sharpe_ratio(equity_curve: pd.Series, periods_per_year: int = 252) -> float:
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0
    rets = equity_curve.pct_change().dropna()
    if rets.std() == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * rets.mean() / rets.std())


# ---------------------------------------------------------------------------
# Standardised Sharpe reporting (Task 4 / SESSION_LOG #22).
#
# The session mixed two conventions, making cross-study comparison unsound:
#   * #11–#17 used the daily-equity convention   (sqrt(252) on daily returns)
#   * #18–#21 used the intraday-bar convention   (sqrt(252*7) on 1h-bar returns)
# From here on BOTH numbers are emitted next to each other for every run.
# No silent convention changes between studies.
# ---------------------------------------------------------------------------
INTRADAY_BARS_PER_YEAR = 252 * 7   # ~7 tradeable 1h bars per session


def sharpe_intraday_bar(equity_curve: pd.Series) -> float:
    """Sharpe on raw 1h-bar returns, annualised by sqrt(252*7)."""
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0
    r = equity_curve.pct_change().dropna()
    if r.std() == 0:
        return 0.0
    return float(np.sqrt(INTRADAY_BARS_PER_YEAR) * r.mean() / r.std())


def sharpe_daily(equity_curve: pd.Series) -> float:
    """Sharpe on the equity curve resampled to daily close, sqrt(252)."""
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0
    daily = equity_curve.resample("1D").last().dropna()
    r = daily.pct_change().dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(np.sqrt(252) * r.mean() / r.std())


def expectancy(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    return float(trades["return_pct"].mean())


def summarize(trades: pd.DataFrame, equity_curve: pd.Series) -> dict:
    return {
        "trades": int(len(trades)),
        "win_rate": round(win_rate(trades), 4),
        "profit_factor": round(profit_factor(trades), 4),
        "expectancy": round(expectancy(trades), 6),
        "max_drawdown": round(max_drawdown(equity_curve), 4),
        "cagr": round(cagr(equity_curve), 4),
        "sharpe": round(sharpe_ratio(equity_curve), 4),
        "sharpe_intraday_bar": round(sharpe_intraday_bar(equity_curve), 4),
        "sharpe_daily": round(sharpe_daily(equity_curve), 4),
        "final_equity": round(float(equity_curve.iloc[-1]) if not equity_curve.empty else 0.0, 2),
    }
