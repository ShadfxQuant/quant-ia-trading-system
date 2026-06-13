"""
Donchian breakout engine — Turtle-style 20/10 channel breakout.

Lab finding (Part 8.30 Phase 5):
    On GC=F, Donchian 20/10 hits +30.1% CAGR vs our pullback's +13.3%.
    Gap of +17pp — biggest cross-engine finding so far.

Original Turtle rules (Richard Dennis, 1983):
    Entry: Close > rolling N-day high (default N=20)
    Exit:  Close < rolling M-day low  (default M=10)
    Stop:  2× ATR from entry (Turtle's "1N rule")
    Sizing: ATR-volatility-adjusted (Turtle's unit sizing)

Our adaptation for gate-testing against the pullback engine:
    - Symmetric long + short (Turtle was long-only futures by default)
    - Same exit ladder structure (stop/TP1/TP2/time) for engine consistency
    - ATR-adaptive stop (2× ATR)
    - Per-symbol Donchian periods configurable

Output columns:
    donchian_Signal     ∈ {-1, 0, +1}
    donchian_SizeMult   ATR-inverse scaled
    donchian_PyramidOK  False (single shot)
    donchian_PyramidCap 1
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from config.settings import DONCHIAN
from strategies.exit_profile import ExitProfile


def exit_profile_for(cfg=DONCHIAN) -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=cfg.stop_loss_pct,
        partial_tp_pct=cfg.partial_tp_pct,
        partial_tp_size=cfg.partial_tp_size,
        final_tp_pct=cfg.final_tp_pct,
        final_tp_size=cfg.final_tp_size,
        move_stop_to_be_after_partial=cfg.move_stop_to_be_after_partial,
        trailing_stop_enabled=cfg.trailing_stop_enabled,
        trailing_logic_type=cfg.trailing_logic_type,
        trailing_starts_at=cfg.trailing_starts_at,
        max_hold_bars=cfg.max_hold_bars,
    )


def generate_signals(df: pd.DataFrame, cfg=DONCHIAN) -> pd.DataFrame:
    out = df.copy()
    high_n = out["High"].rolling(cfg.entry_window).max().shift(1)
    low_n  = out["Low"].rolling(cfg.entry_window).min().shift(1)
    long_break  = out["Close"] > high_n
    short_break = out["Close"] < low_n

    # Filter: only fire on the FIRST bar of a breakout (not every bar above the level)
    fresh_long  = long_break  & ~long_break.shift(1).fillna(False)
    fresh_short = short_break & ~short_break.shift(1).fillna(False)

    # Liquidity floor
    vol_ma = out["Volume"].rolling(20).mean()
    liq_ok = (out["Volume"] > vol_ma * 0.5) if "Volume" in out else pd.Series(True, index=out.index)

    signal = pd.Series(0, index=out.index, dtype=int)
    signal[fresh_long  & liq_ok] = 1
    signal[fresh_short & liq_ok] = -1

    # Size mult ATR-inverse
    atr = out.get("ATR", pd.Series(index=out.index, dtype=float))
    close = out["Close"]
    if atr.notna().sum() > 0:
        atr_pct = (atr / close).ffill().fillna(0.02)
        median_atr = atr_pct.expanding(50).median().fillna(atr_pct.median())
        size_mult = (median_atr / atr_pct.replace(0, np.nan)).clip(lower=0.3, upper=1.5).fillna(1.0)
    else:
        size_mult = pd.Series(1.0, index=out.index)

    out["donchian_Signal"]     = signal
    out["donchian_SizeMult"]   = size_mult
    out["donchian_PyramidOK"]  = False
    out["donchian_PyramidCap"] = 1
    out["donchian_HighN"]      = high_n
    out["donchian_LowN"]       = low_n
    return out


def donchian_signals(df: pd.DataFrame, cfg=DONCHIAN) -> pd.DataFrame:
    return generate_signals(df, cfg)
