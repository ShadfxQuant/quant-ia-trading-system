"""
Subperiod / regime classifier.

Maps the IA exponential-vs-logarithmic framework onto five discrete regimes:

    GROWTH        -> exponential expansion (strong + bullish)
    SLOWDOWN      -> reduced exponential growth (weakening uptrend)
    DISTRIBUTION  -> topping / divergence between price and trend
    CRASH         -> logarithmic decay (strong + bearish)
    STABILIZATION -> low-momentum range (post-crash or pre-trend)

The classifier consumes the indicators produced by `core.indicators` and
returns a per-bar regime label plus per-regime boolean flags.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd

from config.settings import REGIME


class Regime(str, Enum):
    GROWTH = "growth"
    SLOWDOWN = "slowdown"
    DISTRIBUTION = "distribution"
    CRASH = "crash"
    STABILIZATION = "stabilization"
    UNDEFINED = "undefined"


def classify_regime(df: pd.DataFrame, cfg=REGIME) -> pd.DataFrame:
    """
    Return a copy of `df` with a `Regime` column attached.

    Decision logic (evaluated row-wise on indicator values):
      * Strong positive slope + EMA above SMA -> GROWTH
      * Positive but weakening slope          -> SLOWDOWN
      * EMA still above SMA but momentum
        flips negative + volatility spike     -> DISTRIBUTION
      * Strong negative slope + EMA below SMA -> CRASH
      * Slope and divergence near zero        -> STABILIZATION
    """
    out = df.copy()
    n = len(out)

    slope = out["EMA_slope"].to_numpy()
    div = out["Deviation"].to_numpy()             # (EMA - SMA) / SMA
    mom = out["Momentum"].to_numpy()
    vol_ratio = out["Vol_ratio"].to_numpy()

    labels = np.full(n, Regime.UNDEFINED.value, dtype=object)

    for i in range(n):
        s, d, m, v = slope[i], div[i], mom[i], vol_ratio[i]
        if any(map(lambda x: x is None or (isinstance(x, float) and np.isnan(x)),
                   (s, d, m, v))):
            continue

        bullish_struct = d > 0
        bearish_struct = d < 0
        vol_expansion = v > cfg.volatility_spike

        if bullish_struct and s > cfg.slope_strong and m > cfg.momentum_strong:
            labels[i] = Regime.GROWTH.value
        elif bearish_struct and s < -cfg.slope_strong and m < -cfg.momentum_strong:
            labels[i] = Regime.CRASH.value
        elif bullish_struct and 0 < s <= cfg.slope_strong:
            labels[i] = Regime.SLOWDOWN.value
        elif bullish_struct and m < 0 and vol_expansion:
            labels[i] = Regime.DISTRIBUTION.value
        elif abs(s) < cfg.slope_weak and abs(d) < cfg.divergence_weak:
            labels[i] = Regime.STABILIZATION.value
        elif bearish_struct and s < 0:
            # weak bearish trend -> still treat as crash-leaning slowdown
            labels[i] = Regime.SLOWDOWN.value if s > -cfg.slope_strong else Regime.CRASH.value
        else:
            labels[i] = Regime.STABILIZATION.value

    out["Regime"] = labels
    out["Is_bullish_regime"] = out["Regime"].isin(
        [Regime.GROWTH.value, Regime.SLOWDOWN.value]
    )
    out["Is_bearish_regime"] = out["Regime"].isin(
        [Regime.CRASH.value, Regime.DISTRIBUTION.value]
    )
    out["Is_tradeable"] = out["Is_bullish_regime"] | out["Is_bearish_regime"]
    return out


def regime_summary(df: pd.DataFrame) -> pd.Series:
    """Distribution of regime labels (useful for diagnostics)."""
    return df["Regime"].value_counts(normalize=True).round(3)
