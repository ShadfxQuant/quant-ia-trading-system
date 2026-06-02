"""
TradingView-fed automatic trading signal system.

Uses multi-indicator confluence (EMA trend, MACD, RSI, Stochastic, ADX, Bollinger, VWAP, ATR)
to emit BUY / SELL / HOLD signals. High win-rate is targeted via strict confluence:
a signal only fires when the majority of independent indicators agree.

Data source: TradingView via `tvdatafeed`. Falls back to `yfinance` if unavailable.

Usage:
    python tv_auto_trader.py --symbol BTCUSDT --exchange BINANCE --interval 1h
    python tv_auto_trader.py --symbol AAPL    --exchange NASDAQ  --interval 15m --loop 60
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd

Signal = Literal["BUY", "SELL", "HOLD"]


# --------------------------------------------------------------------------- #
# Data fetch
# --------------------------------------------------------------------------- #
def fetch_tv(symbol: str, exchange: str, interval: str, n_bars: int = 500) -> pd.DataFrame:
    try:
        from tvDatafeed import Interval, TvDatafeed
    except ImportError as e:
        raise ImportError("pip install --upgrade git+https://github.com/rongardF/tvdatafeed") from e

    tv_interval = {
        "1m": Interval.in_1_minute, "3m": Interval.in_3_minute, "5m": Interval.in_5_minute,
        "15m": Interval.in_15_minute, "30m": Interval.in_30_minute, "45m": Interval.in_45_minute,
        "1h": Interval.in_1_hour, "2h": Interval.in_2_hour, "3h": Interval.in_3_hour,
        "4h": Interval.in_4_hour, "1d": Interval.in_daily, "1w": Interval.in_weekly,
        "1M": Interval.in_monthly,
    }[interval]

    tv = TvDatafeed()  # anonymous; pass username/password for premium intervals
    df = tv.get_hist(symbol=symbol, exchange=exchange, interval=tv_interval, n_bars=n_bars)
    if df is None or df.empty:
        raise RuntimeError(f"No data from TradingView for {exchange}:{symbol} {interval}")
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]]


def fetch_yf(symbol: str, interval: str, period: str = "60d") -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
    df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]].dropna()


# --------------------------------------------------------------------------- #
# Indicators (pure numpy/pandas — no TA-Lib dependency)
# --------------------------------------------------------------------------- #
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, fast=12, slow=26, sig=9):
    line = ema(s, fast) - ema(s, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def stochastic(h, l, c, k=14, d=3):
    ll = l.rolling(k).min()
    hh = h.rolling(k).max()
    kline = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    return kline, kline.rolling(d).mean()


def atr(h, l, c, n=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(h, l, c, n=14):
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(h, l, c, n)
    plus_di = 100 * pd.Series(plus_dm, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), plus_di, minus_di


def bollinger(s: pd.Series, n=20, k=2):
    m = s.rolling(n).mean()
    sd = s.rolling(n).std()
    return m, m + k * sd, m - k * sd


def vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum_v = df["volume"].cumsum().replace(0, np.nan)
    return (tp * df["volume"]).cumsum() / cum_v


# --------------------------------------------------------------------------- #
# Signal engine
# --------------------------------------------------------------------------- #
@dataclass
class SignalReport:
    signal: Signal
    score: int           # net bull - bear votes
    confidence: float    # 0..1
    price: float
    stop: float
    target: float
    votes: dict[str, int]   # +1 bull / -1 bear / 0 neutral per indicator
    ts: pd.Timestamp


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi"] = rsi(out["close"], 14)
    out["macd"], out["macd_sig"], out["macd_hist"] = macd(out["close"])
    out["stk"], out["std"] = stochastic(out["high"], out["low"], out["close"])
    out["adx"], out["pdi"], out["mdi"] = adx(out["high"], out["low"], out["close"])
    out["bb_mid"], out["bb_up"], out["bb_lo"] = bollinger(out["close"])
    out["vwap"] = vwap(out)
    out["atr"] = atr(out["high"], out["low"], out["close"])
    return out


def evaluate(df: pd.DataFrame, min_score: int = 5) -> SignalReport:
    """
    Vote across 8 independent indicators. A trade fires only on strong confluence,
    which is what gives the system its high win-rate bias (at the cost of frequency).
    """
    f = build_features(df).iloc[-1]
    prev = build_features(df).iloc[-2]
    votes: dict[str, int] = {}

    # 1. EMA trend stack
    if f.ema20 > f.ema50 > f.ema200:
        votes["ema_stack"] = 1
    elif f.ema20 < f.ema50 < f.ema200:
        votes["ema_stack"] = -1
    else:
        votes["ema_stack"] = 0

    # 2. Price vs EMA200 (regime filter)
    votes["regime"] = 1 if f.close > f.ema200 else -1

    # 3. MACD histogram momentum
    if f.macd_hist > 0 and f.macd_hist > prev.macd_hist:
        votes["macd"] = 1
    elif f.macd_hist < 0 and f.macd_hist < prev.macd_hist:
        votes["macd"] = -1
    else:
        votes["macd"] = 0

    # 4. RSI — avoid extremes (no chasing) but require momentum
    if 50 < f.rsi < 70:
        votes["rsi"] = 1
    elif 30 < f.rsi < 50:
        votes["rsi"] = -1
    else:
        votes["rsi"] = 0

    # 5. Stochastic cross
    if f.stk > f.std and f.stk < 80:
        votes["stoch"] = 1
    elif f.stk < f.std and f.stk > 20:
        votes["stoch"] = -1
    else:
        votes["stoch"] = 0

    # 6. ADX trend strength + direction
    if f.adx > 20 and f.pdi > f.mdi:
        votes["adx"] = 1
    elif f.adx > 20 and f.mdi > f.pdi:
        votes["adx"] = -1
    else:
        votes["adx"] = 0

    # 7. Bollinger position (mean-reversion guard against buying tops)
    if f.close < f.bb_up and f.close > f.bb_mid:
        votes["bb"] = 1
    elif f.close > f.bb_lo and f.close < f.bb_mid:
        votes["bb"] = -1
    else:
        votes["bb"] = 0

    # 8. VWAP confirmation (institutional reference)
    votes["vwap"] = 1 if f.close > f.vwap else -1

    score = sum(votes.values())
    bulls = sum(1 for v in votes.values() if v == 1)
    bears = sum(1 for v in votes.values() if v == -1)
    confidence = max(bulls, bears) / len(votes)

    if score >= min_score:
        sig: Signal = "BUY"
        stop = f.close - 1.5 * f.atr
        target = f.close + 3.0 * f.atr      # 2:1 reward:risk
    elif score <= -min_score:
        sig = "SELL"
        stop = f.close + 1.5 * f.atr
        target = f.close - 3.0 * f.atr
    else:
        sig = "HOLD"
        stop = target = float("nan")

    return SignalReport(
        signal=sig, score=score, confidence=confidence,
        price=float(f.close), stop=float(stop), target=float(target),
        votes=votes, ts=df.index[-1],
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def render(r: SignalReport, symbol: str) -> str:
    arrow = {"BUY": "▲ BUY ", "SELL": "▼ SELL", "HOLD": "· HOLD"}[r.signal]
    lines = [
        f"[{datetime.now():%H:%M:%S}] {symbol} bar={r.ts}",
        f"  {arrow}   price={r.price:.4f}   score={r.score:+d}/8   conf={r.confidence:.0%}",
    ]
    if r.signal != "HOLD":
        rr = abs(r.target - r.price) / abs(r.price - r.stop)
        lines.append(f"  stop={r.stop:.4f}  target={r.target:.4f}  R:R={rr:.2f}")
    lines.append("  votes: " + "  ".join(f"{k}={v:+d}" for k, v in r.votes.items()))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="e.g. BTCUSDT, AAPL, EURUSD")
    ap.add_argument("--exchange", default="BINANCE", help="TV exchange, e.g. BINANCE, NASDAQ, FX")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--bars", type=int, default=500)
    ap.add_argument("--min-score", type=int, default=5, help="confluence threshold (max 8)")
    ap.add_argument("--source", choices=["tv", "yf"], default="tv")
    ap.add_argument("--loop", type=int, default=0, help="poll every N seconds; 0 = run once")
    args = ap.parse_args()

    def run_once():
        if args.source == "tv":
            try:
                df = fetch_tv(args.symbol, args.exchange, args.interval, args.bars)
            except Exception as e:
                print(f"[tv failed: {e}] falling back to yfinance")
                df = fetch_yf(args.symbol, args.interval)
        else:
            df = fetch_yf(args.symbol, args.interval)

        r = evaluate(df, min_score=args.min_score)
        print(render(r, f"{args.exchange}:{args.symbol}"))
        return r

    if args.loop <= 0:
        run_once()
        return
    last_bar = None
    while True:
        try:
            r = run_once()
            if r.ts != last_bar and r.signal != "HOLD":
                print(f"  >>> NEW {r.signal} SIGNAL <<<")
                last_bar = r.ts
        except Exception as e:
            print(f"[error] {e}")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
