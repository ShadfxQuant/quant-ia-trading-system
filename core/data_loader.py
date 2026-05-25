"""
Market data loader.

Pulls OHLCV from yfinance, caches to disk, and returns a clean DataFrame
indexed by datetime with columns: Open, High, Low, Close, Volume.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

from config.settings import DATA


# yfinance restricts intraday history. Map each intraday interval to its max period.
_INTRADAY_PERIODS = {
    "1m": "7d",
    "2m": "60d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
    "1h": "730d",
    "90m": "60d",
}


def _cache_path(symbol: str, interval: str) -> str:
    os.makedirs(DATA.raw_dir, exist_ok=True)
    return os.path.join(DATA.raw_dir, f"{symbol}_{interval}.csv")


def load_symbol(
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    interval: Optional[str] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Download (or load cached) OHLCV data for a single symbol."""
    start = start or DATA.start
    end = end or DATA.end
    interval = interval or DATA.interval
    path = _cache_path(symbol, interval)

    if not force_refresh and os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if not df.empty:
            return _normalize(df)

    if yf is None:
        raise RuntimeError(
            "yfinance is not installed. Run `pip install -r requirements.txt`."
        )

    if interval in _INTRADAY_PERIODS:
        raw = yf.download(
            symbol,
            period=_INTRADAY_PERIODS[interval],
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    else:
        raw = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    if raw is None or raw.empty:
        raise ValueError(f"No data returned from yfinance for symbol={symbol!r}.")

    # yfinance returns a MultiIndex when multiple tickers are involved; flatten it.
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    raw = _normalize(raw)
    raw.to_csv(path)
    return raw


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.title)
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep].copy()
    df = df.dropna(how="any")
    df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)
    return df


def load_universe(symbols=None, **kwargs) -> dict[str, pd.DataFrame]:
    """Load every configured symbol; return a {symbol: DataFrame} mapping."""
    symbols = symbols or DATA.symbols
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            out[sym] = load_symbol(sym, **kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"[data_loader] Failed to load {sym}: {exc}")
    return out
