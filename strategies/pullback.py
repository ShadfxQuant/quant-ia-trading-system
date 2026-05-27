"""
Production Pullback Engine — Deterministic-Only Execution.

Edge generator: EMA(50)/SMA(130) structure + deviation pullback + momentum
re-acceleration. The HMM stays in the dataframe (P_bull, P_bear, P_range,
HMM_state) for context and future research, but does NOT scale size, block
trades, or affect pyramiding. RVOL stays in the dataframe for dashboards
and trade metadata, also without affecting execution.

VWAP is institutional pyramid confirmation — it gates *additional stacks*,
never initial entries.

Long entry — all four required:
    * Bullish structure (EMA > SMA, slope > 0, recent higher high).
    * Pullback proximity:  |Close - EMA| / EMA <= pullback_band.
    * Imbalance:           (EMA - SMA) / SMA >= imbalance_min.
    * Momentum re-accel:   Δ momentum > 0.

Short entry (rare, deterministic only):
    * Bearish structure AND deterministic regime ∈ {crash, distribution}.
    * Symmetric pullback / imbalance / momentum-down conditions.

Pyramiding (additional stacks on an already-open same-direction position):
    All required (regardless of HMM):
        * bullish structure
        * regime ∈ {growth, slowdown}
        * Close > VWAP                   ← institutional confirmation
        * Momentum > 0
    Capped at `max_pyramid_positions` AND `capital_cap_pct` of equity.

Output columns (consumed by the portfolio backtester):
    pullback_Signal       ∈ {-1, 0, +1}
    pullback_SizeMult     fixed at 1.0 (deterministic, no scaling)
    pullback_PyramidOK    bool — VWAP-confirmed pyramid green light
    pullback_PyramidCap   per-bar dynamic cap

Diagnostic columns (dashboard / research only — not consumed by execution):
    pullback_Confidence       max HMM probability for context
    pullback_VwapAlignment    +1 above, -1 below
    pullback_RvolAtBar        passthrough of RVOL for downstream logging
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import PULLBACK
from strategies.exit_profile import ExitProfile


def exit_profile_for(cfg=PULLBACK) -> ExitProfile:
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


def generate_signals(df: pd.DataFrame, cfg=PULLBACK) -> pd.DataFrame:
    out = df.copy()

    # ----- Phase 4 P1: ATR-normalized thresholds (cross-symbol portability) -----
    # When enabled, expresses pullback_band, imbalance_min, AND stop_loss_pct
    # as multiples of current ATR/Close. This is the single biggest portability
    # lever — it lets the same strategy ship on assets with different vol
    # regimes (SPY/QQQ/IWM) without manual threshold retuning.
    if cfg.use_atr_normalized and "ATR" in out.columns:
        atr_pct = (out["ATR"] / out["Close"]).ffill().fillna(0.0)
        pullback_band_eff = atr_pct * cfg.pullback_atr_mult
        imbalance_min_eff = atr_pct * cfg.imbalance_atr_mult
        stop_pct_override = atr_pct * cfg.stop_atr_mult
        out["pullback_AtrPct"] = atr_pct
    # ----- Layer 2: Entry Sensitivity Engine (RegimeScore-based) -----
    # Mutually exclusive with ATR normalization. When neither is on, use fixed.
    elif cfg.use_adaptive_entry and "RegimeScore" in out.columns:
        score = out["RegimeScore"].fillna(0.5)
        expansion = (score - 0.5) * 2.0
        pb_mult = (1.0 + cfg.adaptive_pullback_swing * expansion).clip(0.5, 1.6)
        im_mult = (1.0 - cfg.adaptive_imbalance_swing * expansion).clip(0.5, 1.6)
        pullback_band_eff = cfg.pullback_band * pb_mult
        imbalance_min_eff = cfg.imbalance_min * im_mult
        stop_pct_override = pd.Series(cfg.stop_loss_pct, index=out.index)
    else:
        pullback_band_eff = pd.Series(cfg.pullback_band, index=out.index)
        imbalance_min_eff = pd.Series(cfg.imbalance_min, index=out.index)
        stop_pct_override = pd.Series(cfg.stop_loss_pct, index=out.index)

    # Surface effective thresholds + per-bar stop override for the engine.
    out["pullback_PullbackBandEff"] = pullback_band_eff
    out["pullback_ImbalanceMinEff"] = imbalance_min_eff
    out["pullback_StopPctOverride"] = stop_pct_override

    # ----- Deterministic entry conditions (now per-bar adaptive) -----
    pullback   = out["Price_dev"].abs() <= pullback_band_eff
    imb_long   = out["Deviation"] >= imbalance_min_eff
    imb_short  = out["Deviation"] <= -imbalance_min_eff

    # Momentum re-acceleration: default fires on any positive Δ-momentum bar.
    # When `use_momentum_crossup`, fire ONLY on the bar that crosses from
    # decel (Δ ≤ 0) to accel (Δ > 0) — the inflection. Earlier entry, more
    # runner room, fewer signals.
    mom_delta = out["Momentum"].diff()
    if cfg.use_momentum_crossup:
        mom_up = (mom_delta > 0) & (mom_delta.shift(1).fillna(0) <= 0)
        mom_down = (mom_delta < 0) & (mom_delta.shift(1).fillna(0) >= 0)
    else:
        mom_up = mom_delta > 0
        mom_down = mom_delta < 0

    if cfg.use_regime_bypass and "RegimeScore" in out.columns:
        bypass = out["RegimeScore"].fillna(0.0) >= cfg.regime_bypass_threshold
        mom_up = mom_up | bypass

    # ----- Rollover guard: block longs when EMA50 is rolling over -----
    # Even with bullish structure (EMA50 > SMA130), if EMA50's slope has
    # turned negative for ≥3 bars the trend is breaking down faster than
    # the lagging structure check. March 2026 fired pullback longs into
    # exactly this setup (EMA50>SMA130 but slope down) and lost ~$80K
    # in two bars. This guard kills that specific failure mode.
    ema_slope = out["EMA"].diff() if "EMA" in out.columns else pd.Series(0.0, index=out.index)
    long_slope_ok = (ema_slope.rolling(3).mean() > 0).fillna(False)
    short_slope_ok = (ema_slope.rolling(3).mean() < 0).fillna(False)

    long_signal = (
        out["Is_bullish_structure"]
        & pullback & imb_long & mom_up
        & long_slope_ok               # ← new rollover guard
    )
    # Shorts now mirror longs — same structural test, no extra crash-regime
    # gate. The previous `Is_bearish_regime` requirement meant shorts only
    # fired in catastrophic markets, so the engine was effectively long-only
    # (171 longs vs 6 shorts on 2.83y of GLD).
    short_signal = (
        out["Is_bearish_structure"]
        & pullback & imb_short & mom_down
        & short_slope_ok              # ← mirror of rollover guard
    )

    out["pullback_Signal"] = 0
    out.loc[long_signal, "pullback_Signal"] = 1
    out.loc[short_signal, "pullback_Signal"] = -1

    # ----- Sizing (chain of institutional multipliers) -----
    #   base × vol_target × vix_leverage
    # Each layer is independently toggleable; missing columns are 1.0 no-ops.
    size_mult = pd.Series(cfg.fixed_size_mult, index=out.index)
    if cfg.use_vol_targeting and "VolTargetMult" in out.columns:
        size_mult = size_mult * out["VolTargetMult"].fillna(1.0)
    if cfg.use_vix_leverage and "VixLeverageMult" in out.columns:
        size_mult = size_mult * out["VixLeverageMult"].fillna(1.0)

    # ----- HMM meta-layer: sizing controller (SESSION_LOG #22) -----
    # Re-bound from #6/#7. Never gates entries — only scales size and (below)
    # the pyramid cap. P_bull buckets → {low, normal, high} multipliers.
    # NaN during the 6-month walk-forward warmup → pass-through (1.0×) when
    # hmm_warmup_pass_through, else treated as the low bucket.
    if cfg.use_hmm_meta and "P_bull" in out.columns:
        p_bull = out["P_bull"]
        warmup_mult = (cfg.size_mult_normal if cfg.hmm_warmup_pass_through
                       else cfg.size_mult_low)
        hmm_bucket = pd.Series(0, index=out.index, dtype=int)      # 0 = normal
        hmm_bucket[p_bull > cfg.size_threshold_high] = 1           # high
        hmm_bucket[p_bull < cfg.size_threshold_low] = -1           # low
        hmm_mult = pd.Series(cfg.size_mult_normal, index=out.index)
        hmm_mult[hmm_bucket == 1] = cfg.size_mult_high
        hmm_mult[hmm_bucket == -1] = cfg.size_mult_low
        nan_mask = ~np.isfinite(p_bull)
        hmm_mult[nan_mask] = warmup_mult
        hmm_bucket[nan_mask] = 0
        size_mult = size_mult * hmm_mult
        out["pullback_HmmSizeMult"] = hmm_mult
        out["pullback_HmmBucket"] = hmm_bucket
    else:
        out["pullback_HmmSizeMult"] = pd.Series(1.0, index=out.index)
        out["pullback_HmmBucket"] = pd.Series(0, index=out.index, dtype=int)

    out["pullback_SizeMult"] = size_mult

    # ----- Pyramiding gates (VWAP-confirmed institutional scaling) -----
    structure_ok = out["Is_bullish_structure"]
    regime_ok = out["Regime"].isin(list(cfg.pyramid_regimes))

    if cfg.pyramid_require_above_vwap and "VWAP" in out.columns:
        above_vwap = out["Close"] > out["VWAP"]
    else:
        above_vwap = pd.Series(True, index=out.index)

    if cfg.pyramid_require_positive_momentum:
        mom_positive = out["Momentum"] > 0
    else:
        mom_positive = pd.Series(True, index=out.index)

    pyramid_ok = structure_ok & regime_ok & above_vwap & mom_positive

    # ----- HMM meta-layer: pyramid disagreement brake (SESSION_LOG #22) -----
    # Disagreement = deterministic regime says trend, HMM posterior says the
    # opposite. On those bars stop adding stacks entirely (cap → 0). This is
    # the mechanism #6 validated for cutting overstay risk; under 2.5×
    # leverage these are the highest-risk bars.
    if cfg.use_hmm_meta and "P_bull" in out.columns:
        p_bull = out["P_bull"]
        p_bear = out["P_bear"] if "P_bear" in out.columns else pd.Series(np.nan, index=out.index)
        bull_reg = out.get("Is_bullish_regime", regime_ok)
        bear_reg = out.get("Is_bearish_regime", pd.Series(False, index=out.index))
        disagreement = (
            (bull_reg & (p_bull < cfg.disagreement_p_bull_threshold)) |
            (bear_reg & (p_bear < cfg.disagreement_p_bear_threshold))
        ).fillna(False)
        pyramid_ok = pyramid_ok & ~disagreement
        out["pullback_HmmDisagree"] = disagreement
    else:
        out["pullback_HmmDisagree"] = pd.Series(False, index=out.index)

    pyramid_cap = pd.Series(cfg.max_pyramid_positions, index=out.index, dtype=int)
    pyramid_cap.loc[~pyramid_ok] = 0
    out["pullback_PyramidOK"] = pyramid_ok
    out["pullback_PyramidCap"] = pyramid_cap

    # ----- Diagnostic columns (informational only) -----
    if "P_bull" in out.columns:
        # Pure max-of-three diagnostic confidence; not used for sizing.
        confidence = out[["P_bull", "P_bear", "P_range"]].max(axis=1).fillna(0.5)
    else:
        confidence = pd.Series(0.5, index=out.index)
    out["pullback_Confidence"] = confidence

    if "VWAP" in out.columns:
        out["pullback_VwapAlignment"] = np.where(
            out["Close"] > out["VWAP"], 1,
            np.where(out["Close"] < out["VWAP"], -1, 0)
        )
    else:
        out["pullback_VwapAlignment"] = 0

    if "RVOL" in out.columns:
        out["pullback_RvolAtBar"] = out["RVOL"]
    else:
        out["pullback_RvolAtBar"] = np.nan

    return out
