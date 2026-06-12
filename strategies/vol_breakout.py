"""
Vol-breakout engine — buys (or shorts) the dip after a volatility shock.

Thesis (from Edge Lab Part 8.26):
    rvol_high_decile (h=100): on TSLA, mean +528 bp, hit 66.9%, t=11.69
    Multiple symbols show similar shape: realized vol breaks into top decile
    of trailing 500-bar history → forward 100-bar returns are positive on
    average, often substantially.

Trader intuition:
    A vol shock means panic. After the panic burns through, the rebound
    captures the recoil. Hold ~100 bars to capture the full move.

LESSONS APPLIED FROM PART 8.27 (orderflow gate failure):
    The lab measured a 100-bar +528 bp edge on TSLA — orders of magnitude
    larger than orderflow's +35 bp / 20 bar. We size exits TO MATCH that
    geometry. Stops in ATR units (volatility-adaptive); TPs scaled to the
    100-bar realized edge; time stop at exactly the lab horizon.

Signal logic:
  Long entry (the panic-buy):
      * rvol_pct: realized_vol(20) > 90th percentile of trailing 500 bars
      * not_already_recovered: Close < recent_high(20) (dip is recent)
      * not_freefall: Close > recent_low(20) * (1 + 0.005) (not catching knife)
      * trend_filter: EMA(50) > EMA(200) preferred (bull regime backdrop)
      * sentiment: RSI > 25 (not deep-crisis)

  Short entry: symmetric, but rarer — only when bear structure clear:
      * rvol_pct same
      * Close > recent_low(20) but well below recent_high
      * EMA(50) < EMA(200)
      * RSI < 75

Output columns:
    vol_breakout_Signal     ∈ {-1, 0, +1}
    vol_breakout_SizeMult   ATR-inverse scaled (smaller on bigger vol bars)
    vol_breakout_PyramidOK  False (single-shot)
    vol_breakout_PyramidCap 1

Exit ladder (geometry matched to edge):
    stop:  −2.0× ATR (volatility-adaptive, was failure mode in 8.27)
    TP1:   +1.5× ATR (~50% off, stop → BE)
    TP2:   +4.0× ATR (final, captures the long-tail bounce)
    time:  100 bars (matches lab horizon exactly)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import VOL_BREAKOUT
from strategies.exit_profile import ExitProfile


def exit_profile_for(cfg=VOL_BREAKOUT) -> ExitProfile:
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


def _realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    lr = np.log(close / close.shift(1))
    return lr.rolling(window).std() * np.sqrt(252 * 6.5)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50.0)


def generate_signals(df: pd.DataFrame, cfg=VOL_BREAKOUT) -> pd.DataFrame:
    out = df.copy()

    close = out["Close"]
    high  = out["High"]
    low   = out["Low"]

    # ----- vol + trend + sentiment -----
    rvol = _realized_vol(close, cfg.rvol_window)
    rvol_high = rvol.rolling(cfg.rvol_percentile_window).quantile(cfg.rvol_pct_threshold)
    ema_fast = close.ewm(span=cfg.ema_fast_period, adjust=False).mean()
    ema_slow = close.ewm(span=cfg.ema_slow_period, adjust=False).mean()
    rsi = _rsi(close)

    recent_high = high.rolling(cfg.recent_extreme_window).max()
    recent_low  = low.rolling(cfg.recent_extreme_window).min()

    # ----- entry conditions -----
    vol_shock = rvol > rvol_high
    bull_struct = ema_fast > ema_slow
    bear_struct = ema_fast < ema_slow

    # LONG: vol shock + we're below the recent high (dip) + above recent low (not freefall) + bull regime + RSI OK
    not_already_recovered = close < recent_high
    not_freefall = close > recent_low * (1 + cfg.freefall_pad_pct)
    long_entry = (
        vol_shock
        & not_already_recovered
        & not_freefall
        & bull_struct
        & (rsi > cfg.rsi_floor)
    )

    # SHORT: vol shock + below recent high + above recent low + bear regime + RSI not over-cold
    short_entry = (
        vol_shock
        & not_already_recovered
        & not_freefall
        & bear_struct
        & (rsi < cfg.rsi_ceiling)
    )

    # ----- size multiplier (smaller on bigger vol shocks) -----
    # ATR-normalize so a 2σ vol day takes half size of a 1σ vol day
    atr = out.get("ATR", pd.Series(index=out.index, dtype=float))
    if atr.notna().sum() == 0:
        atr_pct = pd.Series(0.02, index=out.index)
    else:
        atr_pct = (atr / close).ffill().fillna(0.02)
    median_atr_pct = atr_pct.expanding(50).median().fillna(atr_pct.median())
    size_mult = (median_atr_pct / atr_pct.replace(0, np.nan)).clip(lower=0.3, upper=1.5).fillna(1.0)

    # ----- emit -----
    signal = pd.Series(0, index=out.index, dtype=int)
    signal[long_entry]  = 1
    signal[short_entry] = -1

    out["vol_breakout_Signal"]      = signal
    out["vol_breakout_SizeMult"]    = size_mult
    out["vol_breakout_PyramidOK"]   = False
    out["vol_breakout_PyramidCap"]  = 1

    # diagnostics
    out["vol_breakout_RVol"]       = rvol
    out["vol_breakout_RVolHigh"]   = rvol_high

    return out


def vol_breakout_signals(df: pd.DataFrame, cfg=VOL_BREAKOUT) -> pd.DataFrame:
    return generate_signals(df, cfg)
