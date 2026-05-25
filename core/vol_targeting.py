"""
Volatility targeting — institutional sizing overlay.

Single-purpose module: attach a per-bar `VolTargetMult` column whose value
is the multiplier that converts the asset's realised volatility into a
target portfolio-volatility contribution.

    VolTargetMult = clip(target_vol_annual / realised_vol_annual, 0.5, 2.0)

When current realised vol is BELOW the target (calm market) the multiplier
is > 1 (lever up). When realised vol is ABOVE target (stress) the
multiplier is < 1 (de-risk). Sharpe stays constant; absolute returns scale
because exposure scales inversely to vol.

This is the standard CTA / risk-parity / vol-targeting overlay.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def realised_vol_annual(close: pd.Series, window_bars: int, bars_per_year: int) -> pd.Series:
    """Rolling realised volatility, annualised."""
    rets = close.pct_change()
    return rets.rolling(window_bars, min_periods=max(10, window_bars // 5)).std() * np.sqrt(bars_per_year)


def attach_vol_target_mult(
    df: pd.DataFrame,
    target_vol_annual: float = 0.20,
    window_bars: int = 210,         # ~30 trading days × 7 hours = 210 bars
    bars_per_year: int = 252 * 7,
    cap_low: float = 0.5,
    cap_high: float = 2.0,
) -> pd.DataFrame:
    """Attach `VolTargetMult` and `RealisedVolAnnual` columns.

    `target_vol_annual = 0.20` means 20% annual portfolio vol target.
    Clipped to [0.5, 2.0] so a single position can never blow out under
    a vol collapse (also limits leverage in genuinely calm markets).
    """
    out = df.copy()
    rv = realised_vol_annual(out["Close"], window_bars, bars_per_year)
    mult = (target_vol_annual / rv).clip(lower=cap_low, upper=cap_high).fillna(1.0)
    out["RealisedVolAnnual"] = rv
    out["VolTargetMult"] = mult
    return out
