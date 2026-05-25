"""
Technical indicators that act as proxies for the IA mathematical model.

    EMA           ->  exponential growth baseline
    SMA           ->  equilibrium / fair-value reference
    EMA slope     ->  first derivative of trend (momentum)
    EMA - SMA     ->  residual deviation from expected trend
    rolling vol   ->  volatility / regime-transition signal
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import INDICATORS, STRATEGY


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def slope(series: pd.Series, window: int) -> pd.Series:
    """Normalized slope: average per-bar % change over `window`."""
    return series.pct_change(window) / window


def momentum(series: pd.Series, period: int) -> pd.Series:
    """Rate of change over `period` bars."""
    return series.pct_change(period)


def deviation(price: pd.Series, baseline: pd.Series) -> pd.Series:
    """Residual deviation from the baseline as a fraction of the baseline."""
    return (price - baseline) / baseline


def rolling_volatility(series: pd.Series, window: int) -> pd.Series:
    return series.pct_change().rolling(window=window, min_periods=window).std()


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range — Wilder's classic volatility estimator.

    TR_t = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
    ATR  = simple rolling mean of TR over `window` bars.
    """
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()


def compute_indicators(df: pd.DataFrame, cfg=INDICATORS) -> pd.DataFrame:
    """Attach the full indicator stack to an OHLCV DataFrame."""
    out = df.copy()
    close = out["Close"]

    out["EMA"] = ema(close, cfg.ema_period)
    out["SMA"] = sma(close, cfg.sma_period)
    out["EMA_slope"] = slope(out["EMA"], cfg.slope_window)
    out["Momentum"] = momentum(close, cfg.momentum_period)
    out["Deviation"] = (out["EMA"] - out["SMA"]) / out["SMA"]
    out["Price_dev"] = deviation(close, out["EMA"])
    out["Volatility"] = rolling_volatility(close, cfg.volatility_window)
    out["Vol_mean"] = out["Volatility"].rolling(cfg.volatility_window).mean()
    out["Vol_ratio"] = out["Volatility"] / out["Vol_mean"]
    out["ATR"] = atr(out, window=14)

    # Higher-high / lower-low structural flags over the deviation lookback.
    look = cfg.deviation_window
    out["Recent_high"] = out["High"].rolling(look, min_periods=look).max()
    out["Recent_low"] = out["Low"].rolling(look, min_periods=look).min()
    out["Higher_high"] = out["High"] >= out["Recent_high"].shift(1)
    out["Lower_low"] = out["Low"] <= out["Recent_low"].shift(1)

    # ----- Institutional indicators -----
    # Relative volume: how heavy is current participation vs the rolling baseline.
    rvol_window = STRATEGY.rvol_window
    out["Volume_mean"] = out["Volume"].rolling(rvol_window, min_periods=rvol_window).mean()
    out["RVOL"] = out["Volume"] / out["Volume_mean"]

    # Session-anchored VWAP: cumulative within each trading day, resets daily.
    # This is the institutional definition of intraday VWAP.
    typical_price = (out["High"] + out["Low"] + out["Close"]) / 3.0
    pv = typical_price * out["Volume"]
    if isinstance(out.index, pd.DatetimeIndex):
        session_key = out.index.floor("D")
    else:
        session_key = pd.Series(0, index=out.index)
    out["_session"] = session_key
    out["VWAP"] = (
        pv.groupby(session_key).cumsum() /
        out["Volume"].groupby(session_key).cumsum().replace(0, np.nan)
    )
    out.drop(columns=["_session"], inplace=True)

    return out
