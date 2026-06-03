"""
Edge library — 35+ testable hypotheses across categories.

Categories:
  - TIME_OF_DAY   — hour-of-day, day-of-week effects
  - VOL_REGIME    — volatility-based filters
  - MOMENTUM      — trend/persistence signals
  - MEAN_REV      — reversion at extremes
  - VOLUME        — relative volume anomalies
  - ORDERFLOW     — CVD / tick imbalance / close-position proxies
  - GAMMA_PROXY   — gamma-regime proxies (VIX term, rvol, etc.)
  - CROSS_SYMBOL  — placeholder for cross-asset (skipped for v1)
  - STRUCTURE     — bar structure (inside bar, engulfing, gaps)

Each edge is a callable returning a boolean Series. The lab will
automatically test BOTH long and inverted (short) directions and
pick the higher-edge version per horizon.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from research.edge_lab import EdgeDef


def _hour_eq(df, h): return df["__Hour"] == h
def _hour_in(df, hs): return df["__Hour"].isin(hs)


EDGES: list[EdgeDef] = [
    # ───────────────── TIME OF DAY ─────────────────
    EdgeDef("tod_open_hour", "TIME_OF_DAY",
            "First trading hour (14 UTC = 9 ET open)",
            lambda df: _hour_eq(df, 14)),
    EdgeDef("tod_lunch_lull", "TIME_OF_DAY",
            "12-13 ET lunch hour (16-17 UTC)",
            lambda df: _hour_in(df, [16, 17])),
    EdgeDef("tod_power_hour", "TIME_OF_DAY",
            "Last hour 15-16 ET (19-20 UTC)",
            lambda df: _hour_in(df, [19, 20])),
    EdgeDef("tod_overnight_close", "TIME_OF_DAY",
            "Pre-Asia open 21-22 UTC",
            lambda df: _hour_in(df, [21, 22])),
    EdgeDef("tod_european_open", "TIME_OF_DAY",
            "London open 08-09 UTC",
            lambda df: _hour_in(df, [8, 9])),
    EdgeDef("dow_monday", "TIME_OF_DAY",
            "Monday bias",
            lambda df: df["__Dow"] == 0),
    EdgeDef("dow_friday", "TIME_OF_DAY",
            "Friday bias (weekend risk-off?)",
            lambda df: df["__Dow"] == 4),
    EdgeDef("dow_midweek", "TIME_OF_DAY",
            "Tue/Wed/Thu (no calendar effect)",
            lambda df: df["__Dow"].isin([1, 2, 3])),

    # ───────────────── VOLATILITY REGIME ─────────────────
    EdgeDef("rvol_high_decile", "VOL_REGIME",
            "Realized vol in top 10% of trailing window",
            lambda df: df["__RVol"] > df["__RVol"].rolling(500).quantile(0.90)),
    EdgeDef("rvol_low_decile", "VOL_REGIME",
            "Realized vol in bottom 10% (vol compression)",
            lambda df: df["__RVol"] < df["__RVol"].rolling(500).quantile(0.10)),
    EdgeDef("range_spike", "VOL_REGIME",
            "Bar range > 2σ above 50-bar mean",
            lambda df: df["__RangeZ"] > 2.0),
    EdgeDef("range_contraction", "VOL_REGIME",
            "Bar range < −1σ (squeeze)",
            lambda df: df["__RangeZ"] < -1.0),

    # ───────────────── MOMENTUM ─────────────────
    EdgeDef("momo_5d_strong_up", "MOMENTUM",
            "5-bar return > +1%",
            lambda df: df["__Ret5"] > 0.01),
    EdgeDef("momo_5d_strong_down", "MOMENTUM",
            "5-bar return < −1%",
            lambda df: df["__Ret5"] < -0.01),
    EdgeDef("momo_acceleration_pos", "MOMENTUM",
            "Fast ROC > Slow ROC (accelerating up)",
            lambda df: df["__MomAccel"] > 0.005),
    EdgeDef("momo_acceleration_neg", "MOMENTUM",
            "Fast ROC < Slow ROC (decelerating)",
            lambda df: df["__MomAccel"] < -0.005),
    EdgeDef("trend_above_50sma", "MOMENTUM",
            "Close > SMA50 (uptrend filter)",
            lambda df: df["Close"] > df["__SMA50"]),
    EdgeDef("trend_below_50sma", "MOMENTUM",
            "Close < SMA50 (downtrend filter)",
            lambda df: df["Close"] < df["__SMA50"]),
    EdgeDef("golden_cross_state", "MOMENTUM",
            "SMA50 > SMA200 (bullish regime)",
            lambda df: df["__SMA50"] > df["__SMA200"]),
    EdgeDef("death_cross_state", "MOMENTUM",
            "SMA50 < SMA200 (bearish regime)",
            lambda df: df["__SMA50"] < df["__SMA200"]),

    # ───────────────── MEAN REVERSION ─────────────────
    EdgeDef("rsi_oversold_30", "MEAN_REV",
            "RSI(14) < 30",
            lambda df: df["__RSI"] < 30),
    EdgeDef("rsi_overbought_70", "MEAN_REV",
            "RSI(14) > 70",
            lambda df: df["__RSI"] > 70),
    EdgeDef("rsi_extreme_low_20", "MEAN_REV",
            "RSI(14) < 20 (deep oversold)",
            lambda df: df["__RSI"] < 20),
    EdgeDef("rsi_extreme_high_80", "MEAN_REV",
            "RSI(14) > 80 (deep overbought)",
            lambda df: df["__RSI"] > 80),
    EdgeDef("bbz_below_2", "MEAN_REV",
            "Close > 2σ below 20-day mean",
            lambda df: df["__BBZ"] < -2.0),
    EdgeDef("bbz_above_2", "MEAN_REV",
            "Close > 2σ above 20-day mean",
            lambda df: df["__BBZ"] > 2.0),

    # ───────────────── VOLUME ─────────────────
    EdgeDef("vol_spike_2sd", "VOLUME",
            "Volume > 2σ above 20-bar mean",
            lambda df: df["__VolZ"] > 2.0),
    EdgeDef("vol_dry_neg2sd", "VOLUME",
            "Volume < −1σ (dry-volume drift)",
            lambda df: df["__VolZ"] < -1.0),

    # ───────────────── ORDERFLOW PROXIES ─────────────────
    EdgeDef("cvd_rising_strong", "ORDERFLOW",
            "CVD 20-bar slope > 0 (buying pressure)",
            lambda df: df["__CVD"].diff(20) > df["__CVD"].diff(20).rolling(200).quantile(0.80)),
    EdgeDef("cvd_falling_strong", "ORDERFLOW",
            "CVD 20-bar slope < 0 bottom decile (selling pressure)",
            lambda df: df["__CVD"].diff(20) < df["__CVD"].diff(20).rolling(200).quantile(0.20)),
    EdgeDef("tick_imb_positive", "ORDERFLOW",
            "Tick imbalance 20-bar > 80th pct (aggressive bid)",
            lambda df: df["__TickImb"] > df["__TickImb"].rolling(200).quantile(0.80)),
    EdgeDef("tick_imb_negative", "ORDERFLOW",
            "Tick imbalance < 20th pct (aggressive offer)",
            lambda df: df["__TickImb"] < df["__TickImb"].rolling(200).quantile(0.20)),
    EdgeDef("close_at_high", "ORDERFLOW",
            "Close in top 10% of bar range (aggressive buying)",
            lambda df: df["__ClosePos"] > 0.90),
    EdgeDef("close_at_low", "ORDERFLOW",
            "Close in bottom 10% (aggressive selling)",
            lambda df: df["__ClosePos"] < 0.10),
    EdgeDef("wide_bar_close_high", "ORDERFLOW",
            "Wide range bar (>1.5σ) closing in top 20% — institutional sweep",
            lambda df: (df["__RangeZ"] > 1.5) & (df["__ClosePos"] > 0.80)),
    EdgeDef("wide_bar_close_low", "ORDERFLOW",
            "Wide range bar closing in bottom 20% — institutional dump",
            lambda df: (df["__RangeZ"] > 1.5) & (df["__ClosePos"] < 0.20)),

    # ───────────────── GAMMA REGIME PROXIES ─────────────────
    EdgeDef("vol_compression_then_expansion", "GAMMA_PROXY",
            "Low rvol decile bar — short-gamma pin breakout candidate",
            lambda df: df["__RVol"] < df["__RVol"].rolling(500).quantile(0.20)),
    EdgeDef("rvol_above_iv_proxy", "GAMMA_PROXY",
            "Range expansion + above-trend (vol regime shift)",
            lambda df: (df["__RangeZ"] > 1.0) & (df["Close"] > df["__SMA50"])),

    # ───────────────── STRUCTURE ─────────────────
    EdgeDef("inside_bar", "STRUCTURE",
            "Inside bar (compression — pre-breakout signal)",
            lambda df: (df["High"] < df["High"].shift(1)) & (df["Low"] > df["Low"].shift(1))),
    EdgeDef("outside_bar_close_high", "STRUCTURE",
            "Outside bar closing > prior high (bullish engulfing)",
            lambda df: (df["High"] > df["High"].shift(1)) & (df["Low"] < df["Low"].shift(1)) &
                       (df["Close"] > df["High"].shift(1))),
    EdgeDef("outside_bar_close_low", "STRUCTURE",
            "Outside bar closing < prior low (bearish engulfing)",
            lambda df: (df["High"] > df["High"].shift(1)) & (df["Low"] < df["Low"].shift(1)) &
                       (df["Close"] < df["Low"].shift(1))),

    # ───────────────── STACKED EDGES (combinations) ─────────────────
    EdgeDef("stack_oversold_uptrend", "STACK",
            "RSI<30 AND in golden-cross regime",
            lambda df: (df["__RSI"] < 30) & (df["__SMA50"] > df["__SMA200"])),
    EdgeDef("stack_overbought_downtrend", "STACK",
            "RSI>70 AND in death-cross regime",
            lambda df: (df["__RSI"] > 70) & (df["__SMA50"] < df["__SMA200"])),
    EdgeDef("stack_volspike_powerhour", "STACK",
            "Vol spike during power hour",
            lambda df: (df["__VolZ"] > 1.5) & df["__Hour"].isin([19, 20])),
    EdgeDef("stack_open_strong_momo", "STACK",
            "Open hour + 5-bar momentum positive",
            lambda df: (df["__Hour"] == 14) & (df["__Ret5"] > 0.005)),
    EdgeDef("stack_orderflow_trend_align", "STACK",
            "Close at high + above SMA50 + above SMA200",
            lambda df: (df["__ClosePos"] > 0.85) & (df["Close"] > df["__SMA50"]) &
                       (df["__SMA50"] > df["__SMA200"])),
]
