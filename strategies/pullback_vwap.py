"""
VWAP-enhanced pullback engine (Part 8.36) — production challenger.

Best-state integration of the Part 8.35 finding ("VWAP as a pullback TARGET
beats EMA/SMA as target") into the production pullback engine.

Design for a FAIR fight:
  - Reuses the production pullback signal generation verbatim (same structure
    filter, slope guard, momentum re-accel, sizing chain, HMM meta-layer,
    pyramiding gates, exit ladder).
  - ADDS one entry trigger: in confirmed structure, a pullback that tags the
    session VWAP and closes back across it also fires an entry — even if it
    didn't tag the EMA band. This widens high-quality entries with the
    institutional VWAP reference, which 8.35 showed is a better pullback
    target than the moving average.
  - Everything downstream (size, pyramids, stops, TPs, time-stop) is identical
    to production, so any P&L difference is attributable purely to the VWAP
    entry trigger.

Consumed by run_portfolio as strategy name "pullback_vwap":
  pullback_vwap_Signal / _SizeMult / _PyramidOK / _PyramidCap
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from config.settings import PULLBACK
from strategies.pullback import generate_signals as _base_generate, exit_profile_for as _base_exit


def exit_profile_for(cfg=PULLBACK):
    # identical exit ladder to production — fairness
    return _base_exit(cfg)


def generate_signals(df: pd.DataFrame, cfg=PULLBACK) -> pd.DataFrame:
    # 1) run production pullback to get all base columns (pullback_*)
    out = _base_generate(df, cfg)

    base_sig = out["pullback_Signal"].copy()

    # 2) VWAP-pullback trigger (the enhancement)
    if "VWAP" in out.columns:
        vw = out["VWAP"]
        ema_slope = out["EMA"].diff() if "EMA" in out.columns else pd.Series(0.0, index=out.index)
        long_slope_ok = (ema_slope.rolling(3).mean() > 0).fillna(False)
        short_slope_ok = (ema_slope.rolling(3).mean() < 0).fillna(False)
        mom_delta = out["Momentum"].diff() if "Momentum" in out.columns else pd.Series(0.0, index=out.index)

        low = out["Low"] if "Low" in out.columns else out["Close"]
        high = out["High"] if "High" in out.columns else out["Close"]

        # long: bullish structure, bar dipped to/through VWAP but closed above,
        #       trend slope up, momentum re-accelerating
        vwap_long = (
            out["Is_bullish_structure"]
            & (low <= vw) & (out["Close"] > vw)
            & long_slope_ok
            & (mom_delta > 0)
        )
        # short mirror
        vwap_short = (
            out["Is_bearish_structure"]
            & (high >= vw) & (out["Close"] < vw)
            & short_slope_ok
            & (mom_delta < 0)
        )
    else:
        vwap_long = pd.Series(False, index=out.index)
        vwap_short = pd.Series(False, index=out.index)

    # 3) OR the VWAP trigger into the base signal (only where base was flat)
    sig = base_sig.copy()
    add_long = (sig == 0) & vwap_long
    add_short = (sig == 0) & vwap_short
    sig[add_long] = 1
    sig[add_short] = -1

    # diagnostics: how many entries came from VWAP vs base
    out["pullback_vwap_FromVWAP"] = (add_long | add_short).astype(int)

    # 4) emit pullback_vwap_* schema (size/pyramid identical to production)
    out["pullback_vwap_Signal"] = sig
    out["pullback_vwap_SizeMult"] = out["pullback_SizeMult"]
    out["pullback_vwap_PyramidOK"] = out["pullback_PyramidOK"]
    out["pullback_vwap_PyramidCap"] = out["pullback_PyramidCap"]
    return out
