"""
Per-symbol regime filter — gate the validated pullback/trend engine to
only fire during *tradable* regimes for assets that trade 24/7.

Problem: the SPY/GLD engine has PF 2.5–3 on NYSE-hours bars. On 24/7 perp
bars (PAXGUSDT, BTCUSDT, etc.) the same engine fires on chop hours and
PF drops to 1.20. Research (`_research_paxg_regime`) showed that gating
the engine to (ADX ≥ 25 AND 13:00–20:00 UTC) restores PF to 1.81 on PAXG
with DD 18.3% — i.e. the alpha is intact, the bars need filtering.

Public API:
    apply_regime_filter(df, symbol) -> df
        Zeroes out `pullback_Signal` and `trend_carry_Signal` on bars
        that fall outside the symbol's regime filter. The exit ladder
        and pyramid logic stay unchanged — already-open positions still
        manage themselves on every bar.

Configured via `REGIME_FILTERS` in config.settings — a mapping of
symbol → filter kind. Symbols not in the map are unfiltered (NYSE-hours
assets like SPY/DIA/GLD don't need a filter).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import REGIME_FILTERS


def _compute_adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ADX. Validated identical to the research harness."""
    high, low, close = df["High"], df["Low"], df["Close"].shift(1)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)
    tr = pd.concat([(high - low),
                    (high - close).abs(),
                    (low - close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def _filter_mask(df: pd.DataFrame, kind: str) -> pd.Series:
    if kind == "NONE":
        return pd.Series(True, index=df.index)
    if kind == "NYSE_ONLY":
        h = pd.Series(df.index.hour, index=df.index)
        return (h >= 13) & (h < 20)
    if kind == "NO_ASIA":
        h = pd.Series(df.index.hour, index=df.index)
        return ~((h >= 0) & (h < 7))
    if kind == "ADX_25":
        adx = _compute_adx(df)
        return adx >= 25
    if kind == "ADX_25_NYSE":   # COMBO_F from the research: production default
        h = pd.Series(df.index.hour, index=df.index)
        return (_compute_adx(df) >= 25) & (h >= 13) & (h < 20)
    if kind == "ADX_25_NO_ASIA":
        h = pd.Series(df.index.hour, index=df.index)
        return (_compute_adx(df) >= 25) & ~((h >= 0) & (h < 7))
    if kind == "ADX_25_NO_ASIA_SLOPE":   # COMBO_E: shipped PAXG default
        h = pd.Series(df.index.hour, index=df.index)
        # 4-bar UP-slope persistence only. Tested allowing down-slope too
        # (to enable the new short engine on PAXG) — regressed PAXG from
        # CAGR 86.4% to 35.4% because gold's been mostly trending up over
        # the available 2yr window. PAXG keeps its long-bias filter; GLD
        # (which gets no filter) takes shorts via the strategy logic.
        ema = df["Close"].ewm(span=50, adjust=False).mean()
        slope = ema.diff()
        slope_ok = (slope > 0).rolling(4).sum() >= 4
        return (_compute_adx(df) >= 25) & ~((h >= 0) & (h < 7)) & slope_ok.fillna(False)
    raise ValueError(f"unknown regime filter kind {kind!r}")


def apply_regime_filter(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Mask engine signals on non-tradable regime bars for `symbol`.
    Returns a copy with `pullback_Signal` and `trend_carry_Signal` zeroed
    on filtered bars. Symbols without a configured filter pass through."""
    kind = REGIME_FILTERS.get(symbol.upper())
    if not kind or kind == "NONE":
        return df
    out = df.copy()
    mask = _filter_mask(out, kind).fillna(False)
    if "pullback_Signal" in out.columns:
        out.loc[~mask, "pullback_Signal"] = 0
    if "trend_carry_Signal" in out.columns:
        out.loc[~mask, "trend_carry_Signal"] = 0
    # Surface the filter eligibility as a diagnostic column so the dashboard
    # can show "% of bars currently tradable" per symbol.
    out["regime_eligible"] = mask.astype(bool)
    return out
