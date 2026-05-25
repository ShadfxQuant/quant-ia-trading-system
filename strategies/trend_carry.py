"""
Layer 3 — Trend Capture Module (carry sleeve).

Runs in parallel with the core pullback engine using the SAME alpha-engine
entry conditions but with three structural differences:

  1. **Activation gate**: only fires when `RegimeScore ≥ activation_threshold`
     (a Layer-4 regime multiplier behaviour). In chop regimes the strategy
     contributes nothing — the pullback engine still runs.

  2. **Sizing**: 12% base (vs pullback's 30%) and 50% capital cap (vs
     pullback's 100%). The two strategies can stack — total max combined
     notional = 1.50× equity during high-score expansion regimes.

  3. **Exit profile**: structural only. Wider stop (-4%), small partial
     (close 30% at +8%), macro runner target (+25%), ATR×3.0 trailing
     after partial, and a 1500-bar (~9 month) time stop. The trade dies
     only on structural invalidation, not normal volatility.

Architectural rule: this module never duplicates pullback's exits or
sizing. It is a *carry sleeve* — the asymmetric runner machinery the core
engine intentionally lacks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import TRENDCARRY
from strategies.exit_profile import ExitProfile


def exit_profile_for(cfg=TRENDCARRY) -> ExitProfile:
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
        atr_multiplier=cfg.atr_multiplier,
        max_hold_bars=cfg.max_hold_bars,
    )


def generate_signals(df: pd.DataFrame, cfg=TRENDCARRY) -> pd.DataFrame:
    out = df.copy()

    # ----- Layer 4 activation gate -----
    if "RegimeScore" in out.columns:
        score_ok = out["RegimeScore"] >= cfg.activation_score_threshold
    else:
        # No regime score available → strategy is dormant.
        score_ok = pd.Series(False, index=out.index)

    # ----- Same alpha-engine conditions, slightly looser thresholds -----
    pullback   = out["Price_dev"].abs() <= cfg.pullback_band
    imb_long   = out["Deviation"] >= cfg.imbalance_min
    mom_delta = out["Momentum"].diff()
    if cfg.use_momentum_crossup:
        mom_up = (mom_delta > 0) & (mom_delta.shift(1).fillna(0) <= 0)
    else:
        mom_up = mom_delta > 0

    if cfg.use_regime_bypass and "RegimeScore" in out.columns:
        bypass = out["RegimeScore"].fillna(0.0) >= cfg.regime_bypass_threshold
        mom_up = mom_up | bypass

    long_signal = (
        out["Is_bullish_structure"]
        & pullback & imb_long & mom_up
        & score_ok
    )

    out["trend_carry_Signal"] = 0
    out.loc[long_signal, "trend_carry_Signal"] = 1
    # Sizing chain (mirrors pullback): base × vol_target × vix_leverage.
    size_mult = pd.Series(cfg.fixed_size_mult, index=out.index)
    if cfg.use_vol_targeting and "VolTargetMult" in out.columns:
        size_mult = size_mult * out["VolTargetMult"].fillna(1.0)
    if cfg.use_vix_leverage and "VixLeverageMult" in out.columns:
        size_mult = size_mult * out["VixLeverageMult"].fillna(1.0)
    out["trend_carry_SizeMult"] = size_mult

    # ----- Pyramid gates (looser than pullback; no VWAP requirement) -----
    structure_ok = out["Is_bullish_structure"]
    regime_ok = out["Regime"].isin(list(cfg.pyramid_regimes))
    if cfg.pyramid_require_positive_momentum:
        mom_pos = out["Momentum"] > 0
    else:
        mom_pos = pd.Series(True, index=out.index)
    if cfg.pyramid_require_above_vwap and "VWAP" in out.columns:
        above_vwap = out["Close"] > out["VWAP"]
    else:
        above_vwap = pd.Series(True, index=out.index)

    pyramid_ok = structure_ok & regime_ok & mom_pos & above_vwap & score_ok
    pyramid_cap = pd.Series(cfg.max_pyramid_positions, index=out.index, dtype=int)
    pyramid_cap.loc[~pyramid_ok] = 0
    out["trend_carry_PyramidOK"] = pyramid_ok
    out["trend_carry_PyramidCap"] = pyramid_cap

    # ----- Diagnostics -----
    out["trend_carry_Activation"] = score_ok
    return out
