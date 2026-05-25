"""
Market structure rules.

Bullish structure:
    EMA > SMA, EMA slope > 0, recent higher high.
Bearish structure:
    EMA < SMA, EMA slope < 0, recent lower low.

Returns a structural label per bar that downstream entry logic combines
with regime classification to decide whether to act.
"""

from __future__ import annotations

from enum import Enum

import pandas as pd


class Structure(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


def label_structure(df: pd.DataFrame) -> pd.DataFrame:
    """Attach a `Structure` column to `df` (must already contain indicators)."""
    out = df.copy()

    # "Higher highs forming" = a fresh rolling-window high occurred within the
    # recent lookback (not necessarily on the current bar — pullbacks happen
    # *after* the high). Same idea, mirrored, for lower lows.
    recent_hh = out["Higher_high"].fillna(False).rolling(20, min_periods=1).max().astype(bool)
    recent_ll = out["Lower_low"].fillna(False).rolling(20, min_periods=1).max().astype(bool)

    bullish = (
        (out["EMA"] > out["SMA"])
        & (out["EMA_slope"] > 0)
        & recent_hh
    )
    bearish = (
        (out["EMA"] < out["SMA"])
        & (out["EMA_slope"] < 0)
        & recent_ll
    )

    structure = pd.Series(Structure.NEUTRAL.value, index=out.index, dtype=object)
    structure[bullish] = Structure.BULLISH.value
    structure[bearish] = Structure.BEARISH.value

    out["Structure"] = structure
    out["Is_bullish_structure"] = structure == Structure.BULLISH.value
    out["Is_bearish_structure"] = structure == Structure.BEARISH.value
    return out
