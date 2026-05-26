"""
Market data loader.

Pulls OHLCV from yfinance, caches to disk, and returns a clean DataFrame
indexed by datetime with columns: Open, High, Low, Close, Volume.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import pandas as pd
import requests

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

from config.settings import DATA


# Symbols that should be routed to Binance's public klines API instead of
# yfinance. Anything ending in USDT/USDC/BUSD is a Binance perp/spot pair
# (PAXGUSDT, BTCUSDT, ETHUSDT, ...). yfinance has no data for these.
_BINANCE_QUOTES = ("USDT", "USDC", "BUSD")


def _is_binance_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(_BINANCE_QUOTES)


# Map our internal interval strings to Binance's interval codes.
_BINANCE_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "60m": "1h", "1h": "1h", "90m": "1h",
    "4h": "4h", "1d": "1d",
}


def _binance_klines(symbol: str, interval: str,
                    start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    """Page through Binance's public /api/v3/klines (1000-row limit per call)."""
    iv = _BINANCE_INTERVALS.get(interval, "1h")
    start_ms = int(pd.Timestamp(start or "2020-01-01", tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end or pd.Timestamp.utcnow(), tz="UTC").timestamp() * 1000)
    # data-api.binance.vision is Binance's public market-data mirror — same
    # payload as api.binance.com but not geo-blocked from US IPs (api.binance.com
    # returns HTTP 451 from the US/UK/CA).
    url = "https://data-api.binance.vision/api/v3/klines"
    rows: list = []
    cursor = start_ms
    while cursor < end_ms:
        r = requests.get(url, params={
            "symbol": symbol.upper(), "interval": iv,
            "startTime": cursor, "endTime": end_ms, "limit": 1000,
        }, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        last_open = batch[-1][0]
        if last_open <= cursor:
            break
        cursor = last_open + 1
        time.sleep(0.15)  # be polite to the public endpoint
    if not rows:
        raise ValueError(f"No klines returned from Binance for {symbol!r}.")
    df = pd.DataFrame(rows, columns=[
        "open_time", "Open", "High", "Low", "Close", "Volume",
        "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
    ])
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("Open", "High", "Low", "Close", "Volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


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

    # Route Binance pairs (PAXGUSDT, BTCUSDT, ...) to the public klines API.
    # yfinance has no data for these symbols.
    if _is_binance_symbol(symbol):
        raw = _binance_klines(symbol, interval, start, end)
        raw = _normalize(raw)
        raw.to_csv(path)
        return raw

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
    # `utc=True` forces a single timezone — pandas 2.x raises on mixed-tz
    # indexes, which yfinance occasionally returns when symbols span DST
    # boundaries or trade on different listings (e.g. GLD vs SPY).
    df.index = pd.to_datetime(df.index, utc=True)
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
