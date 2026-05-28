"""
STRATEGY A — Asian Session Mean-Reversion (gold).

Thesis: during 00:00–07:00 UTC, gold lacks directional flow (Asian session
muted) and ADX < 25 confirms chop. In these bars price tends to mean-revert
to a 20-bar rolling mean. The COMBO_E regime filter excludes these bars
from the trend engine — this strategy exploits them directly.

Entry universe: bars where time IN 00:00–07:00 UTC AND ADX(14) < 25.

Public API matches the existing strategy contract:
    generate_signals(df, cfg=GOLD_ASIAN_MEANREV) -> df  (adds *_Signal col)
    exit_profile_for(cfg=GOLD_ASIAN_MEANREV)     -> ExitProfile
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from strategies.exit_profile import ExitProfile


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GoldAsianMeanRevConfig:
    name: str = "gold_asian_meanrev"
    # Universe
    asian_start_hour: int = 0      # inclusive UTC
    asian_end_hour: int = 7        # exclusive
    adx_max: float = 25.0
    # Entry
    rolling_window: int = 20
    sigma_threshold: float = 1.0   # close must be ≥ this many σ from mean
    # Exit
    stop_pct: float = 0.015        # 1.5%
    final_tp_pct: float = 0.008    # 0.8%
    max_hold_bars: int = 4
    # Portfolio sizing — must use canonical names so run_portfolio reads them.
    base_size_pct: float = 0.15
    capital_cap_pct: float = 0.30
    max_pyramid_positions: int = 1   # no pyramiding for mean-rev


GOLD_ASIAN_MEANREV = GoldAsianMeanRevConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_adx_local(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ADX — same formula as core/regime_filter, kept local so this
    module doesn't reach across packages for indicators."""
    high, low, close = df["High"], df["Low"], df["Close"].shift(1)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)
    tr = pd.concat([(high - low), (high - close).abs(), (low - close).abs()],
                   axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def exit_profile_for(cfg: GoldAsianMeanRevConfig = GOLD_ASIAN_MEANREV) -> ExitProfile:
    # No partial TP for this strategy — single hard target.
    return ExitProfile(
        stop_loss_pct=cfg.stop_pct,
        partial_tp_pct=cfg.final_tp_pct,
        partial_tp_size=1.0,            # close 100% at the target
        final_tp_pct=cfg.final_tp_pct,
        final_tp_size=0.0,
        move_stop_to_be_after_partial=False,
        trailing_stop_enabled=False,
        trailing_logic_type="none",
        trailing_starts_at="immediately",
        max_hold_bars=cfg.max_hold_bars,
    )


def generate_signals(df: pd.DataFrame,
                     cfg: GoldAsianMeanRevConfig = GOLD_ASIAN_MEANREV) -> pd.DataFrame:
    out = df.copy()
    sig_col = f"{cfg.name}_Signal"

    # ----- Universe gate -----
    h = pd.Series(out.index.hour, index=out.index)
    in_asian = (h >= cfg.asian_start_hour) & (h < cfg.asian_end_hour)
    adx = _compute_adx_local(out)
    chop = adx < cfg.adx_max
    universe = in_asian & chop

    # ----- Mean + σ on Close -----
    roll = out["Close"].rolling(cfg.rolling_window)
    mean = roll.mean()
    std = roll.std()

    long_setup = universe & (out["Close"] < mean - cfg.sigma_threshold * std)
    short_setup = universe & (out["Close"] > mean + cfg.sigma_threshold * std)

    out[sig_col] = 0
    out.loc[long_setup, sig_col] = 1
    out.loc[short_setup, sig_col] = -1
    # Drop any signal where mean or std is NaN (warm-up bars)
    out.loc[mean.isna() | std.isna(), sig_col] = 0
    return out
