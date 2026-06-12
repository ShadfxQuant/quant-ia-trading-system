"""
Orderflow-exhaustion engine — fades aggressive selling/buying when it exhausts.

Thesis (from Edge Lab Part 8.18, validated on 23+ symbols):
    When tick imbalance OR CVD slope hits the extreme tail of the trailing
    200-bar distribution, the next 20 bars revert. Lab measurements:
      tick_imb_negative h=20: mean +35bp, hit 60-65%, t=6.11 / 23 symbols
      cvd_falling_strong h=20: mean +33bp, hit 62-64%, t=5.92 / 24 symbols

This is the SYSTEMATIC version of "absorption/exhaustion at the level"
that tape-readers (not-a-lil-fish, SMB Capital) describe discretionarily.

DIFFERENT FROM PULLBACK ENGINE:
  - Pullback: bull/bear regime + price near EMA + momentum reaccel (slow,
    holds 100s of bars, +100bp+ per trade)
  - Orderflow exhaustion: ANY regime + extreme orderflow tail + bottoming
    (fast, holds 20 bars max, +35-50bp per trade)

Both engines are complementary; they fire on different signal families
and share the execution engine's portfolio-level capital cap.

Signal logic:
  Long entry — all required:
      * sell_exhausted: TickImb < 20th-pct(200) OR CVD_slope_20 < 20th-pct(200)
      * not_in_freefall: Close > recent_low(50) (i.e. not catching a knife)
      * liquidity_ok: Volume > 0.5 × volume_ma(20)
      * sentiment_ok: RSI > 25 (skip deep-crisis tape)

  Short entry — symmetric (buy exhaustion, blow-off top):
      * buy_exhausted: TickImb > 80th-pct(200) OR CVD_slope_20 > 80th-pct(200)
      * not_in_parabolic: Close < recent_high(50)
      * liquidity_ok: same
      * sentiment_ok: RSI < 75

Output columns (consumed by execution/portfolio.py):
    orderflow_exhaustion_Signal     ∈ {-1, 0, +1}
    orderflow_exhaustion_SizeMult   fixed 1.0 (no scaling)
    orderflow_exhaustion_PyramidOK  False (single-shot only, no stacking)
    orderflow_exhaustion_PyramidCap fixed at 1 (no stacking)

Exit ladder (tighter than pullback because edge is smaller per trade):
    stop:  −1.5% (or 1.5× ATR if use_atr_stop)
    TP1:   +1.0% (50% off, stop → BE)
    TP2:   +2.5%
    time:  20 bars
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import ORDERFLOW
from strategies.exit_profile import ExitProfile


def exit_profile_for(cfg=ORDERFLOW) -> ExitProfile:
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


def _cvd(df: pd.DataFrame) -> pd.Series:
    """Pseudo-CVD via bar body direction × volume."""
    sign = np.where(df["Close"] > df["Open"], 1.0,
            np.where(df["Close"] < df["Open"], -1.0, 0.0))
    return (sign * df["Volume"]).cumsum()


def _tick_imbalance(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Rolling tick-rule volume imbalance."""
    sign = np.sign(df["Close"].diff().fillna(0))
    return (sign * df["Volume"]).rolling(window).sum()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50.0)


def generate_signals(df: pd.DataFrame, cfg=ORDERFLOW) -> pd.DataFrame:
    out = df.copy()

    # ----- proxies -----
    cvd = _cvd(out)
    cvd_slope = cvd - cvd.shift(cfg.cvd_slope_window)
    tick_imb = _tick_imbalance(out, cfg.tick_window)
    rsi = _rsi(out["Close"])

    # ----- percentile bands (trailing window) -----
    pct_window = cfg.percentile_window
    ti_low  = tick_imb.rolling(pct_window).quantile(cfg.exhaustion_pct_low)
    ti_high = tick_imb.rolling(pct_window).quantile(cfg.exhaustion_pct_high)
    cvd_low  = cvd_slope.rolling(pct_window).quantile(cfg.exhaustion_pct_low)
    cvd_high = cvd_slope.rolling(pct_window).quantile(cfg.exhaustion_pct_high)

    # ----- context filters -----
    vol_ma = out["Volume"].rolling(20).mean()
    liquidity_ok = out["Volume"] > (vol_ma * cfg.min_liquidity_frac)
    recent_low_50  = out["Low"].rolling(50).min()
    recent_high_50 = out["High"].rolling(50).max()
    not_freefall    = out["Close"] > recent_low_50  * (1 + cfg.freefall_pad_pct)
    not_parabolic   = out["Close"] < recent_high_50 * (1 - cfg.freefall_pad_pct)
    sentiment_long_ok  = rsi > cfg.rsi_floor       # skip deep-crisis tape
    sentiment_short_ok = rsi < cfg.rsi_ceiling     # skip euphoric tape

    # ----- entry conditions -----
    sell_exhausted = (tick_imb < ti_low) | (cvd_slope < cvd_low)
    buy_exhausted  = (tick_imb > ti_high) | (cvd_slope > cvd_high)

    long_entry  = sell_exhausted & not_freefall  & liquidity_ok & sentiment_long_ok
    short_entry = buy_exhausted  & not_parabolic & liquidity_ok & sentiment_short_ok

    # ----- emit columns (mirrors pullback.py schema) -----
    signal = pd.Series(0, index=out.index, dtype=int)
    signal[long_entry]  = 1
    signal[short_entry] = -1

    out["orderflow_exhaustion_Signal"]     = signal
    out["orderflow_exhaustion_SizeMult"]   = 1.0
    out["orderflow_exhaustion_PyramidOK"]  = False
    out["orderflow_exhaustion_PyramidCap"] = 1

    # ----- diagnostic columns (dashboards only) -----
    out["orderflow_TickImb"]    = tick_imb
    out["orderflow_TickImbLow"] = ti_low
    out["orderflow_TickImbHigh"]= ti_high
    out["orderflow_CVDSlope"]   = cvd_slope
    out["orderflow_CVDLow"]     = cvd_low
    out["orderflow_CVDHigh"]    = cvd_high

    return out


# Backwards-compat alias matching the pullback module convention
def orderflow_exhaustion_signals(df: pd.DataFrame, cfg=ORDERFLOW) -> pd.DataFrame:
    return generate_signals(df, cfg)
