"""
Lever 4 — VIX-conditional dynamic leverage.

Loads ^VIX from yfinance, aligns to the trading dataframe, and emits a
per-bar leverage multiplier that **layers on top of base leverage**:

    Final notional × Base leverage × VIX multiplier × VolTarget multiplier

Multiplier function (smooth, clipped):
    mult(VIX) = clip( (NEUTRAL - VIX) / SENSITIVITY + 1.0, 0.5, 1.5 )

Defaults:
    NEUTRAL_VIX = 18    (long-run SPY ~equity-neutral level)
    SENSITIVITY = 12    (gentle gradient)

Examples (with defaults):
    VIX = 10  →  mult ≈ 1.50  (calm, lever up)
    VIX = 14  →  mult ≈ 1.33
    VIX = 18  →  mult = 1.00  (neutral)
    VIX = 22  →  mult ≈ 0.67
    VIX = 30  →  mult ≈ 0.50  (stress, cut exposure)

This is the genuine institutional measure of forward-looking risk
(implied vol from SPX options). It complements ATR normalisation
(per-trade backward-looking) and VolTargeting (portfolio-level realised
vol). VIX adds the missing forward-looking dimension.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.data_loader import load_symbol


DEFAULT_NEUTRAL_VIX = 18.0
DEFAULT_SENSITIVITY = 12.0
DEFAULT_CAP_LOW = 0.5
DEFAULT_CAP_HIGH = 1.5


def _compute_mult(vix: pd.Series,
                  neutral: float = DEFAULT_NEUTRAL_VIX,
                  sensitivity: float = DEFAULT_SENSITIVITY,
                  cap_low: float = DEFAULT_CAP_LOW,
                  cap_high: float = DEFAULT_CAP_HIGH) -> pd.Series:
    raw = (neutral - vix) / sensitivity + 1.0
    return raw.clip(lower=cap_low, upper=cap_high)


def attach_vix_leverage_mult(
    df: pd.DataFrame,
    neutral: float = DEFAULT_NEUTRAL_VIX,
    sensitivity: float = DEFAULT_SENSITIVITY,
    cap_low: float = DEFAULT_CAP_LOW,
    cap_high: float = DEFAULT_CAP_HIGH,
) -> pd.DataFrame:
    """Attach `VIX` and `VixLeverageMult` columns.

    Falls back to neutral multiplier (1.0) on data failure so the rest
    of the pipeline continues to function. The strategy size_mult
    multiplication is a no-op in that case.
    """
    out = df.copy()
    try:
        vix_df = load_symbol("^VIX")
        vix_series = vix_df["Close"]
    except Exception:
        out["VIX"] = float("nan")
        out["VixLeverageMult"] = 1.0
        return out

    # yfinance ^VIX may be at a different frequency than our 1H bars
    # (daily for free tier on long histories). Forward-fill onto our index.
    vix_aligned = vix_series.reindex(out.index, method="ffill").bfill()
    mult = _compute_mult(vix_aligned, neutral, sensitivity, cap_low, cap_high)

    out["VIX"] = vix_aligned
    out["VixLeverageMult"] = mult.fillna(1.0)
    return out
