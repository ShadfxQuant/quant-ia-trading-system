"""
Strategy 2 — Mean-Reversion-on-Extremes.

Thesis: when price snaps far below its EMA inside a still-bullish regime,
statistical mean reversion is high-probability and short-lived.

Long entry (all required):
  * Deterministic regime ∈ {growth, slowdown}     (still in an uptrend overall)
  * (Close − EMA) / EMA ≤ deviation_threshold     (default ≤ −0.012, i.e., ≥1.2% below EMA)
  * Close < SMA                                   (price below longer equilibrium too)
  * Intrabar buying response: Close ≥ Low + 0.4 × (High − Low)

(No shorts in this version — SPY's structural drift is up.)

The HMM meta layer drives sizing and pyramiding identically to pullback:
  * P_bull > 0.70 → 1.5×
  * P_bull < 0.30 → 0.5×
  * disagreement → no pyramiding

Output columns:
    meanrev_Signal       ∈ {0, +1}
    meanrev_SizeMult     contextual size multiplier
    meanrev_Confidence   direction-aware HMM probability
    meanrev_Disagreement bool — divergence flag
    meanrev_PyramidOK    bool — pyramiding allowed on this bar
    meanrev_PyramidCap   per-bar dynamic pyramid cap
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import MEANREV
from strategies.exit_profile import ExitProfile


def _size_mult(p_bull: float, cfg=MEANREV) -> float:
    if not np.isfinite(p_bull):
        return cfg.size_mult_normal
    if p_bull > cfg.size_threshold_high:
        return cfg.size_mult_high
    if p_bull < cfg.size_threshold_low:
        return cfg.size_mult_low
    return cfg.size_mult_normal


def exit_profile_for(cfg=MEANREV) -> ExitProfile:
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


def generate_signals(df: pd.DataFrame, cfg=MEANREV) -> pd.DataFrame:
    out = df.copy()

    # ----- Deterministic gates -----
    bull_regime_ok = out["Regime"].isin(list(cfg.long_regimes))
    extreme_below = out["Price_dev"] <= cfg.deviation_threshold
    below_sma = (out["Close"] < out["SMA"]) if cfg.require_close_below_sma else pd.Series(True, index=out.index)

    if cfg.require_intrabar_buying:
        bar_range = (out["High"] - out["Low"]).replace(0, np.nan)
        intrabar = ((out["Close"] - out["Low"]) / bar_range) >= cfg.intrabar_threshold
        intrabar = intrabar.fillna(False)
    else:
        intrabar = pd.Series(True, index=out.index)

    long_signal = bull_regime_ok & extreme_below & below_sma & intrabar

    out["meanrev_Signal"] = 0
    out.loc[long_signal, "meanrev_Signal"] = 1

    # ----- HMM meta layer -----
    # use_hmm_meta=False neutralises the layer (size 1.0×, no disagreement
    # brake) so config C in SESSION_LOG #22 is a clean "no HMM" comparison.
    use_hmm = getattr(cfg, "use_hmm_meta", True)
    has_hmm = use_hmm and "P_bull" in out.columns
    p_bull = out["P_bull"] if has_hmm else pd.Series(np.nan, index=out.index)
    p_bear = out["P_bear"] if has_hmm else pd.Series(np.nan, index=out.index)

    if has_hmm:
        size_mult = p_bull.apply(lambda v: _size_mult(v, cfg)).fillna(cfg.size_mult_normal)
    else:
        size_mult = pd.Series(cfg.size_mult_normal, index=out.index)
    out["meanrev_SizeMult"] = size_mult

    if has_hmm:
        confidence = p_bull.fillna(0.5)
    else:
        confidence = pd.Series(0.5, index=out.index)
    out["meanrev_Confidence"] = confidence

    # Disagreement: bullish regime but HMM bearish.
    if has_hmm:
        disagreement = bull_regime_ok & (p_bull < cfg.disagreement_p_bull_threshold)
    else:
        disagreement = pd.Series(False, index=out.index)
    out["meanrev_Disagreement"] = disagreement

    # Pyramiding gate.
    pyramid_regime_ok = out["Regime"].isin(list(cfg.pyramid_regimes))
    pyramid_ok = pyramid_regime_ok & ~disagreement
    pyramid_cap = pd.Series(cfg.max_pyramid_positions, index=out.index, dtype=int)
    pyramid_cap.loc[disagreement] = 0
    pyramid_cap.loc[~pyramid_regime_ok] = 0
    out["meanrev_PyramidOK"] = pyramid_ok
    out["meanrev_PyramidCap"] = pyramid_cap

    return out
