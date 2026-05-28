"""
STRATEGY B — ADX Threshold Breakout (gold).

Thesis: when ADX crosses up through 25 after a sustained chop period,
gold has often woken up. Entering at the threshold crossing captures the
beginning of a fresh trend before the COMBO_E regime filter would
qualify the bar (COMBO_E requires ADX ≥ 25 + slope persistence +
non-Asia hours; this strategy fires at the moment of the threshold flip).
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from strategies.exit_profile import ExitProfile


@dataclass(frozen=True)
class GoldAdxBreakoutConfig:
    name: str = "gold_adx_breakout"
    # Universe
    adx_threshold: float = 25.0
    chop_lookback: int = 3          # min bars of ADX<threshold required before breakout
    exclude_asian_start: int = 0
    exclude_asian_end: int = 7
    # Exit
    stop_atr_mult: float = 2.0
    partial_tp_pct: float = 0.03    # close 50%, move stop to BE
    final_tp_pct: float = 0.08
    partial_tp_size: float = 0.5
    final_tp_size: float = 0.5
    max_hold_bars: int = 48
    base_size_pct: float = 0.20
    capital_cap_pct: float = 0.40
    max_pyramid_positions: int = 2


GOLD_ADX_BREAKOUT = GoldAdxBreakoutConfig()


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


def exit_profile_for(cfg: GoldAdxBreakoutConfig = GOLD_ADX_BREAKOUT) -> ExitProfile:
    # ATR-based stop is approximated as fixed pct via the harness — we pass
    # a placeholder pct that the research script overrides with ATR×N.
    # For the StrategySpec-driven backtest we set stop_loss_pct to a sane
    # default; the actual stop computed per-bar is handled inside the strategy
    # via a _StopPctOverride column (see generate_signals).
    return ExitProfile(
        stop_loss_pct=0.03,             # default; per-bar override below
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
                     cfg: GoldAdxBreakoutConfig = GOLD_ADX_BREAKOUT) -> pd.DataFrame:
    out = df.copy()
    sig_col = f"{cfg.name}_Signal"
    stop_col = f"{cfg.name}_StopPctOverride"

    adx = _compute_adx_local(out)
    # chop streak = consecutive bars where ADX < threshold
    below = adx < cfg.adx_threshold
    # Running count: increments when below, resets to 0 when above
    chop_bars = below.astype(int).groupby((~below).cumsum()).cumsum()
    # Shift by 1 — "chop bars on PRIOR bar"
    chop_prior = chop_bars.shift(1)
    adx_prior = adx.shift(1)

    breakout = (adx >= cfg.adx_threshold) & (adx_prior < cfg.adx_threshold) & \
               (chop_prior >= cfg.chop_lookback)

    # Direction filters using EMA50 slope
    if "EMA" in out.columns:
        ema = out["EMA"]
    else:
        ema = out["Close"].ewm(span=50, adjust=False).mean()
    ema_slope = ema.diff()

    h = pd.Series(out.index.hour, index=out.index)
    not_asian = ~((h >= cfg.exclude_asian_start) & (h < cfg.exclude_asian_end))

    long_setup = breakout & (ema_slope > 0) & (out["Close"] > ema) & not_asian
    short_setup = breakout & (ema_slope < 0) & (out["Close"] < ema) & not_asian

    out[sig_col] = 0
    out.loc[long_setup, sig_col] = 1
    out.loc[short_setup, sig_col] = -1

    # Per-bar stop override: ATR × cfg.stop_atr_mult expressed as pct of close.
    if "ATR" in out.columns:
        atr_pct = (out["ATR"] * cfg.stop_atr_mult / out["Close"]).clip(0.005, 0.10)
    else:
        atr_pct = pd.Series(0.03, index=out.index)
    out[stop_col] = atr_pct
    return out
