"""
Regime Score — proxy for GEX/DEX market-state interpretation.

This module produces a single scalar per bar in [0, 1] that the upstream
layers (Entry Sensitivity Engine, Trend Capture Module, Regime Multiplier)
read to modulate their behaviour.

Semantic mapping:
    1.0  →  low-gamma / trend expansion / directional regime
              · widen entry thresholds, accelerate confirmation
              · activate trend-carry module
              · allow aggressive pyramiding
    0.5  →  neutral baseline                       (mid-gamma)
    0.0  →  high-gamma / pinning / mean-reversion / chop
              · tighten entry thresholds
              · disable trend-carry
              · throttle pyramiding

GEX/DEX hook
============
The actual GEX/DEX data lives outside yfinance. When you wire in a real
options-microstructure feed (SpotGamma, SqueezeMetrics, CBOE-direct,
etc.), replace `compute_regime_score` with a version that consumes those
inputs. The DOWNSTREAM LAYERS DO NOT CHANGE — they only consume the
single `RegimeScore` column. That is the entire point of this module.

Until that integration is done, the proxy below uses indicators we
already compute:
    * Vol_ratio       — realised volatility expansion
    * |EMA_slope|     — directional persistence (1st derivative of trend)
    * |Deviation|     — residual EMA-SMA gap (trend strength)
    * ATR expansion   — true-range expansion vs rolling baseline
Each component is normalised to [0, 1] and weighted into the composite.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Component normalisers
# ---------------------------------------------------------------------------

def _clip01(s: pd.Series) -> pd.Series:
    return s.clip(lower=0.0, upper=1.0)


def _vol_expansion_score(vol_ratio: pd.Series) -> pd.Series:
    """Vol_ratio = realised σ / mean realised σ.

    0.7 → 0  (compression)
    1.7 → 1  (expansion)
    """
    return _clip01((vol_ratio.fillna(1.0) - 0.7) / 1.0)


def _slope_strength_score(ema_slope: pd.Series, ref: float = 0.0008) -> pd.Series:
    """|EMA_slope| normalised against a 'strong slope' reference."""
    return _clip01(ema_slope.abs().fillna(0.0) / ref)


def _deviation_score(deviation: pd.Series, ref: float = 0.02) -> pd.Series:
    """|Deviation| (EMA-SMA gap) normalised — larger gap = stronger trend."""
    return _clip01(deviation.abs().fillna(0.0) / ref)


def _atr_expansion_score(atr: pd.Series, lookback: int = 50) -> pd.Series:
    """ATR(now) / mean(ATR, lookback).  0.8 → 0, 1.4 → 1."""
    baseline = atr.rolling(lookback, min_periods=10).mean()
    expansion = atr / baseline
    return _clip01((expansion.fillna(1.0) - 0.8) / 0.6)


# ---------------------------------------------------------------------------
# Composite regime score
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "vol":      0.25,
    "slope":    0.30,
    "div":      0.25,
    "atr":      0.20,
}


def compute_regime_score(
    df: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.Series:
    """Compute a per-bar regime score in [0, 1].

    Requires columns: `Vol_ratio`, `EMA_slope`, `Deviation`, `ATR`. These
    are produced by `core.indicators.compute_indicators`.

    The score is intentionally smooth — no hard thresholds. Layers above
    decide their own cutoffs (e.g. ≥0.6 = expansion, ≤0.4 = chop).
    """
    w = weights or DEFAULT_WEIGHTS
    vol_s   = _vol_expansion_score(df["Vol_ratio"])
    slope_s = _slope_strength_score(df["EMA_slope"])
    div_s   = _deviation_score(df["Deviation"])
    atr_s   = _atr_expansion_score(df["ATR"]) if "ATR" in df.columns else pd.Series(0.5, index=df.index)

    score = (
        vol_s * w["vol"]
        + slope_s * w["slope"]
        + div_s * w["div"]
        + atr_s * w["atr"]
    )
    return score.fillna(0.5).rename("RegimeScore")


def attach_regime_score(df: pd.DataFrame) -> pd.DataFrame:
    """Attach `RegimeScore` and convenience boolean columns to df."""
    out = df.copy()
    out["RegimeScore"] = compute_regime_score(out)
    out["Regime_Expansion"] = out["RegimeScore"] >= 0.6
    out["Regime_Chop"] = out["RegimeScore"] <= 0.4
    out["Regime_Neutral"] = (out["RegimeScore"] > 0.4) & (out["RegimeScore"] < 0.6)
    return out
