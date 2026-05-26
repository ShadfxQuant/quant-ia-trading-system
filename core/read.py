"""
Per-symbol "Read" — a synthesized narrative of the system's current view.

Most of the time there is no live signal. The Read tells you what the
engine *thinks* anyway: bias (bullish/bearish/neutral), strength (strong
trend vs chop), regime eligibility (how much of the last 24h was tradable),
macro tilt (does the news backdrop agree with the bias for this symbol's
polarity), and concrete bar-level conditions that would flip the read.

Public API:
    compute_read(df, symbol, macro=None) -> dict
        Pure function. `df` is a prepared dataframe with EMA/SMA/Close
        already populated (output of prepare_dual + apply_regime_filter).
        `macro` is the dict from worker._snapshot_macro() or None.

The dict shape is intentionally flat + JSON-safe so it serializes straight
into data/state.json and the Cloudflare Worker can render it from
`raw.githubusercontent.com` without computing anything.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.regime_filter import _compute_adx


def _bucket_adx(adx: float) -> str:
    if adx >= 30: return "strong"
    if adx >= 20: return "weak"
    return "chop"


def _bias_from_trend(close: float, ema: float, sma: float, slope: float) -> str:
    """EMA50 vs SMA130 cross + slope sign — classic trend taxonomy."""
    if np.isnan(ema) or np.isnan(sma):
        return "neutral"
    above_ema = close > ema
    ema_over_sma = ema > sma
    slope_up = slope > 0
    bull = above_ema and ema_over_sma and slope_up
    bear = (not above_ema) and (not ema_over_sma) and (not slope_up)
    if bull: return "bullish"
    if bear: return "bearish"
    return "neutral"


def compute_read(df: pd.DataFrame, symbol: str,
                 macro: dict | None = None) -> dict:
    if len(df) < 50:
        return {"bias": "neutral", "strength": "chop", "narrative":
                "Not enough bars yet to form a read.", "regime_pct_24h": 0.0,
                "adx": None, "macro_tilt": "n/a", "flip": []}

    last = df.iloc[-1]
    close = float(last["Close"])
    ema = float(last.get("EMA", float("nan")))
    sma = float(last.get("SMA", float("nan")))

    # ADX — trend strength
    adx_series = _compute_adx(df)
    adx = float(adx_series.iloc[-1]) if not np.isnan(adx_series.iloc[-1]) else 0.0
    strength = _bucket_adx(adx)

    # EMA slope over last 4 bars — direction persistence
    ema_series = df["EMA"] if "EMA" in df.columns else df["Close"].ewm(span=50, adjust=False).mean()
    slope = float(ema_series.iloc[-1] - ema_series.iloc[-5]) if len(ema_series) >= 5 else 0.0

    bias = _bias_from_trend(close, ema, sma, slope)

    # Regime eligibility over last 24h (24 hourly bars)
    if "regime_eligible" in df.columns:
        last24 = df["regime_eligible"].tail(24)
        regime_pct = float(last24.mean()) * 100.0 if len(last24) else 0.0
    else:
        regime_pct = 100.0  # symbols without a filter are always eligible

    # Macro tilt — does the news backdrop agree with the bias?
    macro_tilt = "neutral"
    if macro and macro.get("verdict") and macro["verdict"] != "NEUTRAL":
        try:
            from core.news_macro import is_inverse_macro
            inverse = is_inverse_macro(symbol)
        except Exception:
            inverse = False
        v = macro["verdict"]
        # For equities, RISK_ON favours bullish. For gold (inverse), RISK_OFF does.
        if inverse:
            favours_bull = (v == "RISK_OFF")
        else:
            favours_bull = (v == "RISK_ON")
        if bias == "bullish" and favours_bull: macro_tilt = "supports"
        elif bias == "bearish" and not favours_bull: macro_tilt = "supports"
        elif bias == "neutral": macro_tilt = "neutral"
        else: macro_tilt = "conflicts"

    # Concrete conditions that would flip the read
    flip: list[str] = []
    if bias == "bullish":
        flip.append(f"Close < EMA50 ({ema:,.2f})")
        flip.append(f"ADX falls below 20 (now {adx:.1f})")
        if strength != "chop":
            flip.append("EMA50 crosses back below SMA130")
    elif bias == "bearish":
        flip.append(f"Close > EMA50 ({ema:,.2f})")
        flip.append(f"ADX falls below 20 (now {adx:.1f})")
        flip.append("EMA50 crosses back above SMA130")
    else:
        flip.append(f"Close + ADX both push above EMA50 with ADX ≥ 20")
        flip.append(f"Or close below EMA50 with ADX rising past 20")

    # Narrative paragraph — what the trader actually reads
    bias_word = {"bullish": "leans long", "bearish": "leans short",
                 "neutral": "is neutral"}[bias]
    strength_word = {"strong": "and the trend is strong",
                     "weak": "but the trend is only weak",
                     "chop": "and the tape is choppy"}[strength]
    regime_word = ("regime filter is mostly green"
                   if regime_pct >= 50 else
                   "regime filter is mostly blocking — only "
                   f"{regime_pct:.0f}% of the last 24h was tradable")
    macro_word = {
        "supports": "Macro backdrop supports this read.",
        "conflicts": "Macro backdrop conflicts with this read — size cautiously.",
        "neutral": "",
        "n/a": "",
    }[macro_tilt]
    narrative = (f"{symbol} {bias_word} (ADX {adx:.1f}, close {close:,.2f} "
                 f"vs EMA50 {ema:,.2f}) {strength_word}. The {regime_word}. "
                 f"{macro_word}").strip()

    return {
        "bias": bias,
        "strength": strength,
        "adx": round(adx, 2),
        "ema_slope_4bar": round(slope, 4),
        "regime_pct_24h": round(regime_pct, 1),
        "macro_tilt": macro_tilt,
        "flip": flip,
        "narrative": narrative,
    }
