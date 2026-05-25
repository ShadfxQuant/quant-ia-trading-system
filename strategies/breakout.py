"""
Strategy 2 — Institutional Breakout / Expansion.

Thesis: trade range escapes when institutional participation (RVOL)
confirms the move and volatility is expanding.

Entry — long (all required):
    * Close > rolling N-bar high (default N=20).
    * RVOL > rvol_long_min (default 1.2).
    * Close > VWAP (institutional bias above session anchor).
    * vol_ratio > vol_ratio_min (default 1.2).
    * P_bull > hmm_long_min (default 0.60).
    * EMA > SMA AND EMA slope > 0.

Entry — short (mirror, all required):
    * Close < rolling N-bar low.
    * RVOL > rvol_short_min (default 1.3).
    * Close < VWAP.
    * vol_ratio > vol_ratio_min.
    * P_bear > hmm_short_min (default 0.60).
    * EMA < SMA AND slope < 0.

Pyramiding gate:
    * P_bull > pyramid_hmm_min (default 0.75) for longs (P_bear for shorts).
    * RVOL is rising (Δ RVOL > 0) — institutional flow accelerating.

Outputs:
    breakout_Signal      ∈ {-1, 0, +1}
    breakout_SizeMult    HMM-driven sizing multiplier
    breakout_PyramidOK   bool — green light to add a stack on this bar
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import BREAKOUT
from strategies.exit_profile import ExitProfile


def _hmm_size_mult(p_bull: float) -> float:
    if not np.isfinite(p_bull):
        return 1.0
    if p_bull > 0.70:
        return 1.3
    if p_bull < 0.50:
        return 0.5
    return 1.0


def exit_profile_for(cfg=BREAKOUT) -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=cfg.stop_loss_pct,
        partial_tp_pct=cfg.partial_tp_pct,
        partial_tp_size=cfg.partial_tp_size,
        final_tp_pct=cfg.final_tp_pct,
        final_tp_size=cfg.final_tp_size,
        move_stop_to_be_after_partial=cfg.move_stop_to_be_after_partial,
        trailing_stop_enabled=cfg.trailing_stop_enabled,
        trailing_logic_type=cfg.trailing_logic_type,
        trailing_starts_at=cfg.trailing_starts_at,
        max_hold_bars=cfg.max_hold_bars,
    )


def generate_signals(df: pd.DataFrame, cfg=BREAKOUT) -> pd.DataFrame:
    out = df.copy()

    if not {"VWAP", "RVOL", "Vol_ratio"}.issubset(out.columns):
        out["breakout_Signal"] = 0
        out["breakout_SizeMult"] = 1.0
        out["breakout_PyramidOK"] = False
        return out

    n = cfg.lookback_bars
    high_n = out["High"].rolling(n, min_periods=n).max().shift(1)
    low_n  = out["Low"].rolling(n, min_periods=n).min().shift(1)

    rvol_long  = out["RVOL"].fillna(0) > cfg.rvol_long_min
    rvol_short = out["RVOL"].fillna(0) > cfg.rvol_short_min
    vol_expand = out["Vol_ratio"].fillna(0) > cfg.vol_ratio_min

    # HMM gates
    if "P_bull" in out.columns:
        p_bull = out["P_bull"].fillna(0.0)
        p_bear = out["P_bear"].fillna(0.0)
        hmm_long_ok  = p_bull > cfg.hmm_long_min
        hmm_short_ok = p_bear > cfg.hmm_short_min
    else:
        p_bull = pd.Series(np.nan, index=out.index)
        p_bear = pd.Series(np.nan, index=out.index)
        hmm_long_ok  = pd.Series(True, index=out.index)
        hmm_short_ok = pd.Series(True, index=out.index)

    # Trend confirmation reuses existing structure indicators.
    bullish_trend = (out["EMA"] > out["SMA"]) & (out["EMA_slope"] > 0)
    bearish_trend = (out["EMA"] < out["SMA"]) & (out["EMA_slope"] < 0)

    long_breakout = (
        (out["Close"] > high_n)
        & rvol_long
        & (out["Close"] > out["VWAP"])
        & vol_expand
        & hmm_long_ok
        & bullish_trend
    )
    short_breakout = (
        (out["Close"] < low_n)
        & rvol_short
        & (out["Close"] < out["VWAP"])
        & vol_expand
        & hmm_short_ok
        & bearish_trend
    )

    out["breakout_Signal"] = 0
    out.loc[long_breakout, "breakout_Signal"] = 1
    out.loc[short_breakout, "breakout_Signal"] = -1

    # HMM sizing multiplier (use P_bull for longs, P_bear for shorts).
    if "P_bull" in out.columns:
        size_mult = pd.Series(1.0, index=out.index)
        long_mask = out["breakout_Signal"] == 1
        short_mask = out["breakout_Signal"] == -1
        size_mult.loc[long_mask] = out.loc[long_mask, "P_bull"].apply(_hmm_size_mult)
        size_mult.loc[short_mask] = out.loc[short_mask, "P_bear"].apply(_hmm_size_mult)
        out["breakout_SizeMult"] = size_mult
    else:
        out["breakout_SizeMult"] = 1.0

    # Pyramiding gate
    rvol_rising = out["RVOL"].diff() > 0
    if "P_bull" in out.columns:
        pyramid_hmm_long  = p_bull > cfg.pyramid_hmm_min
        pyramid_hmm_short = p_bear > cfg.pyramid_hmm_min
    else:
        pyramid_hmm_long  = pd.Series(True, index=out.index)
        pyramid_hmm_short = pd.Series(True, index=out.index)

    pyramid_long  = pyramid_hmm_long & (rvol_rising if cfg.pyramid_requires_rvol_rising else True)
    pyramid_short = pyramid_hmm_short & (rvol_rising if cfg.pyramid_requires_rvol_rising else True)
    out["breakout_PyramidOK"] = pyramid_long | pyramid_short

    return out
