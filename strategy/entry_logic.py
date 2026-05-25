"""
Entry signal generator with **asymmetric** regime coupling and optional
institutional confirmation layers (RVOL, VWAP, HMM).

Long side: regime is decoupled — structure + pullback + imbalance + momentum
re-acceleration is sufficient. Optional gates: RVOL > rvol_long_threshold,
Close > VWAP, P_bull > hmm_long_threshold.

Short side: regime is required (Is_bearish_regime). Optional gates:
RVOL > rvol_short_threshold, Close < VWAP, P_bear > hmm_short_threshold.

Each filter is toggled via flags in `StrategyConfig` so we can A/B layers.
"""

from __future__ import annotations

import pandas as pd

from config.settings import STRATEGY


def generate_signals(df: pd.DataFrame, cfg=STRATEGY) -> pd.DataFrame:
    """Add `Signal` and supporting diagnostic columns."""
    out = df.copy()

    pullback_long = out["Price_dev"].abs() <= cfg.pullback_band
    imbalance_long = out["Deviation"] >= cfg.imbalance_min
    momentum_up = out["Momentum"].diff() > 0

    pullback_short = out["Price_dev"].abs() <= cfg.pullback_band
    imbalance_short = out["Deviation"] <= -cfg.imbalance_min
    momentum_down = out["Momentum"].diff() < 0

    # ----- Optional institutional confirmation layers -----
    long_extra = pd.Series(True, index=out.index)
    short_extra = pd.Series(True, index=out.index)

    if cfg.use_rvol_filter and "RVOL" in out.columns:
        rvol = out["RVOL"].fillna(0)
        long_extra &= rvol > cfg.rvol_long_threshold
        short_extra &= rvol > cfg.rvol_short_threshold
        out["RVOL_long_pass"] = rvol > cfg.rvol_long_threshold
        out["RVOL_short_pass"] = rvol > cfg.rvol_short_threshold

    if cfg.use_vwap_filter and "VWAP" in out.columns:
        vwap = out["VWAP"]
        long_extra &= out["Close"] > vwap
        short_extra &= out["Close"] < vwap
        out["VWAP_long_pass"] = out["Close"] > vwap
        out["VWAP_short_pass"] = out["Close"] < vwap

    if cfg.use_hmm_filter and "P_bull" in out.columns:
        long_extra &= out["P_bull"].fillna(0) > cfg.hmm_long_threshold
        short_extra &= out["P_bear"].fillna(0) > cfg.hmm_short_threshold
        out["HMM_long_pass"] = out["P_bull"].fillna(0) > cfg.hmm_long_threshold
        out["HMM_short_pass"] = out["P_bear"].fillna(0) > cfg.hmm_short_threshold

    long_signal = (
        out["Is_bullish_structure"]
        & pullback_long & imbalance_long & momentum_up
        & long_extra
    )
    short_signal = (
        out["Is_bearish_structure"]
        & out["Is_bearish_regime"]
        & pullback_short & imbalance_short & momentum_down
        & short_extra
    )

    out["Signal"] = 0
    out.loc[long_signal, "Signal"] = 1
    out.loc[short_signal, "Signal"] = -1

    out["Pullback"] = pullback_long
    out["Imbalance_long"] = imbalance_long
    out["Imbalance_short"] = imbalance_short
    return out
