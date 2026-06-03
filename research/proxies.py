"""
Proxy computations for gamma exposure and order flow.

We don't have a free options chain or L2 book feed, so we build proxies
from what yfinance gives us (OHLCV bars + VIX). These proxies are NOT
substitutes for the real thing — they correlate weakly to moderately with
the true signal but are tradeable on free data.

What's here:
  - vix_term_proxy(): VIX1D / VIX3M ratio. Above 1 = vol backwardation =
    short-gamma regime (markets accelerate). Below 1 = contango = long
    gamma (markets pin). Used as a regime overlay.
  - put_call_proxy(): we approximate via Close vs SMA20 distance — when
    price is far above mean, retail call demand is high (gamma proxy).
  - cvd_proxy(): cumulative volume delta. Assigns each bar's volume a
    sign based on (close - open) direction. Running sum is the
    pseudo-CVD. Tracks buying pressure.
  - tick_imbalance(): rolling sum of sign(close - close_prev) * volume.
    Like CVD but signed by tick direction not body direction.
  - close_position(): (Close - Low) / (High - Low). 0-1. High = aggressive
    buying into close; low = aggressive selling. Used by orderflow
    practitioners as exhaustion/absorption signal.
  - bar_range_z(): z-score of bar range vs trailing 50-bar mean. Wide
    bars with high CVD = institutional sweeps.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def cvd_proxy(df: pd.DataFrame) -> pd.Series:
    """Cumulative Volume Delta proxy via bar body direction."""
    sign = np.where(df["Close"] > df["Open"], 1.0,
            np.where(df["Close"] < df["Open"], -1.0, 0.0))
    signed_vol = sign * df["Volume"]
    return signed_vol.cumsum()


def tick_imbalance(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Rolling tick-rule volume imbalance."""
    sign = np.sign(df["Close"].diff().fillna(0))
    signed_vol = sign * df["Volume"]
    return signed_vol.rolling(window).sum()


def close_position(df: pd.DataFrame) -> pd.Series:
    """Close position within the bar [0, 1]. High = aggressive buying."""
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    return ((df["Close"] - df["Low"]) / rng).clip(0, 1)


def bar_range_z(df: pd.DataFrame, window: int = 50) -> pd.Series:
    """Z-score of bar range vs trailing window mean."""
    rng = df["High"] - df["Low"]
    mu = rng.rolling(window).mean()
    sd = rng.rolling(window).std()
    return ((rng - mu) / sd.replace(0, np.nan)).fillna(0)


def vix_term_proxy(vix_short: pd.Series, vix_long: pd.Series) -> pd.Series:
    """VIX term-structure ratio as gamma-regime proxy.

    Above 1 = backwardation (short-gamma; trending). Below 1 = contango
    (long-gamma; pinning).
    """
    return (vix_short / vix_long.replace(0, np.nan)).fillna(1.0)


def gex_walls_proxy(df: pd.DataFrame, window: int = 100) -> pd.Series:
    """Pseudo gamma-wall via volume profile peaks within a window.

    A real GEX wall is the strike with the largest dealer gamma exposure;
    we approximate by finding the price level with the highest cumulative
    volume in the trailing window. Often these levels act as support /
    resistance the same way GEX walls do.
    """
    if "Volume" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    # bin close prices into 50 buckets, find the modal bucket per window
    out = pd.Series(np.nan, index=df.index)
    closes = df["Close"]
    vols = df["Volume"]
    for i in range(window, len(df)):
        sl_close = closes.iloc[i - window: i]
        sl_vol = vols.iloc[i - window: i]
        bins = pd.cut(sl_close, bins=50)
        vol_by_bin = sl_vol.groupby(bins, observed=True).sum()
        if len(vol_by_bin) and vol_by_bin.max() > 0:
            top_bin = vol_by_bin.idxmax()
            out.iloc[i] = float(top_bin.mid)
    return out


def momentum_acceleration(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    """ROC(fast) - ROC(slow). Positive = accelerating up; negative = decelerating."""
    fast_roc = df["Close"].pct_change(fast)
    slow_roc = df["Close"].pct_change(slow)
    return fast_roc - slow_roc


def realized_vol(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Annualized realized vol of log returns over window."""
    lr = np.log(df["Close"] / df["Close"].shift(1))
    return lr.rolling(window).std() * np.sqrt(252 * 6.5)


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Standard RSI for edge-mining (replaces strategy RSI to keep isolation)."""
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def bollinger_z(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Z-score of close vs trailing window mean (Bollinger band position)."""
    mu = df["Close"].rolling(window).mean()
    sd = df["Close"].rolling(window).std()
    return ((df["Close"] - mu) / sd.replace(0, np.nan)).fillna(0)
