"""
STRATEGY C — Rollover Early Short (gold).

Thesis: by the time EMA50 < SMA130 prints (the structural bear flip), gold
has already shed several percent. We can short earlier when EMA50's 3-bar
slope mean turns negative WHILE EMA50 is still above SMA130 — i.e. the
trend has started rolling but the lagging structural check hasn't caught
up yet. This is a SHORT-ONLY strategy by design.

Exit thesis:
  - SL: +1.5% above entry
  - TP1: −3% (close 50%, BE)
  - TP2: when EMA50 actually crosses below SMA130 (thesis confirmed)
  - Time stop: 48 bars
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from strategies.exit_profile import ExitProfile


@dataclass(frozen=True)
class GoldRolloverShortConfig:
    name: str = "gold_rollover_short"
    # Trigger
    slope_window: int = 3
    adx_min: float = 20.0
    momentum_max: float = 0.0       # momentum_delta < 0 required
    # Exit
    stop_pct: float = 0.015
    partial_tp_pct: float = 0.03
    final_tp_pct: float = 0.06      # used if crossover never confirms within time stop
    partial_tp_size: float = 0.5
    final_tp_size: float = 0.5
    max_hold_bars: int = 48
    base_size_pct: float = 0.20
    capital_cap_pct: float = 0.20
    max_pyramid_positions: int = 1


GOLD_ROLLOVER_SHORT = GoldRolloverShortConfig()


def _compute_adx_local(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"].shift(1)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)
    tr = pd.concat([(high - low), (high - close).abs(), (low - close).abs()],
                   axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def exit_profile_for(cfg: GoldRolloverShortConfig = GOLD_ROLLOVER_SHORT) -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=cfg.stop_pct,
        partial_tp_pct=cfg.partial_tp_pct,
        partial_tp_size=cfg.partial_tp_size,
        final_tp_pct=cfg.final_tp_pct,
        final_tp_size=cfg.final_tp_size,
        move_stop_to_be_after_partial=True,
        trailing_stop_enabled=False,
        trailing_logic_type="none",
        trailing_starts_at="after_partial",
        max_hold_bars=cfg.max_hold_bars,
    )


def generate_signals(df: pd.DataFrame,
                     cfg: GoldRolloverShortConfig = GOLD_ROLLOVER_SHORT) -> pd.DataFrame:
    out = df.copy()
    sig_col = f"{cfg.name}_Signal"

    # Indicator wiring — use whatever's already on the df, recompute if missing.
    ema = out["EMA"] if "EMA" in out.columns else out["Close"].ewm(span=50, adjust=False).mean()
    sma = out["SMA"] if "SMA" in out.columns else out["Close"].rolling(130).mean()
    momentum = out["Momentum"] if "Momentum" in out.columns else out["Close"].pct_change(5)
    adx = _compute_adx_local(out)

    # Rolling slope mean over last N bars
    ema_diff = ema.diff()
    slope_mean = ema_diff.rolling(cfg.slope_window).mean()

    # Structurally still bullish (EMA > SMA), but slope rolled over
    rollover = (ema > sma) & (slope_mean < 0) & (adx >= cfg.adx_min) & \
               (momentum < cfg.momentum_max)

    out[sig_col] = 0
    out.loc[rollover, sig_col] = -1   # short only
    return out
