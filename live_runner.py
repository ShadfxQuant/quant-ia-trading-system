"""
Production live dashboard — Deterministic Pullback Engine.

Surfaces the state actually used by execution plus the institutional
diagnostics (RVOL informational, VWAP for pyramiding, HMM for context).

Usage:
    python -m live_runner               # SPY default
    python -m live_runner SPY --refresh
    python -m live_runner QQQ
"""

from __future__ import annotations

import sys

import pandas as pd

from config.settings import DATA, BACKTEST, PULLBACK
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pullback_profile
from execution.portfolio import run_portfolio, StrategySpec


def _fmt(x, fmt: str = "{:.2f}") -> str:
    return "—" if (x is None or pd.isna(x)) else fmt.format(x)


def render(symbol: str, refresh: bool = False) -> None:
    raw = load_symbol(symbol, force_refresh=refresh)
    df = prepare_dual(raw)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    ts = df.index[-1]

    # Replay the backtest just to know what's open right now.
    strategies = [
        StrategySpec(name="pullback", cfg=PULLBACK, exit_profile=pullback_profile()),
    ]
    bt = run_portfolio(df, strategies, symbol=symbol)
    trades = bt["trades"]
    exposure_curve = bt.get("exposure_curve", pd.Series(dtype=float))

    # Open positions = trades that were force-closed at end_of_data.
    open_now = 0
    if not trades.empty:
        eod = trades[trades["exit_reason"] == "end_of_data"]
        open_now = int(eod.loc[eod.strategy == "pullback", "entry_time"].nunique())
    current_exposure_pct = float(exposure_curve.iloc[-1]) if len(exposure_curve) else 0.0

    close = float(last["Close"])
    ema = float(last["EMA"])
    sma = float(last["SMA"])
    ema_slope = float(last["EMA_slope"])
    momentum = float(last["Momentum"])
    momentum_delta = momentum - float(prev["Momentum"])
    deviation = float(last["Deviation"])
    vol_ratio = float(last.get("Vol_ratio", float("nan")))
    rvol = float(last.get("RVOL", float("nan")))
    vwap = float(last.get("VWAP", float("nan")))
    vwap_align = "ABOVE" if close > vwap else ("BELOW" if close < vwap else "EQUAL")
    structure = str(last.get("Structure", "?"))
    regime = str(last.get("Regime", "?"))
    sig = int(last.get("pullback_Signal", 0)) if not pd.isna(last.get("pullback_Signal", 0)) else 0
    pyramid_ok = bool(last.get("pullback_PyramidOK", False))
    pyramid_cap = int(last.get("pullback_PyramidCap", 0)) \
        if not pd.isna(last.get("pullback_PyramidCap", 0)) else 0

    # Why is pyramid blocked? (decompose the gate for the dashboard)
    pyr_blockers = []
    if not last.get("Is_bullish_structure", False):
        pyr_blockers.append("structure")
    if regime not in PULLBACK.pyramid_regimes:
        pyr_blockers.append("regime")
    if PULLBACK.pyramid_require_above_vwap and close <= vwap:
        pyr_blockers.append("VWAP")
    if PULLBACK.pyramid_require_positive_momentum and momentum <= 0:
        pyr_blockers.append("momentum")

    # HMM (informational)
    p_bull = last.get("P_bull", float("nan"))
    p_bear = last.get("P_bear", float("nan"))
    p_range = last.get("P_range", float("nan"))

    print(f"\n╔══════════════════════════════════════════════════════════════════════╗")
    print(f"║  PRODUCTION DASHBOARD — {symbol}  @  {ts}")
    print(f"╠══════════════════════════════════════════════════════════════════════╣")
    print(f"║  Close            : {close:.2f}")
    print(f"║  EMA(50) / SMA(130): {ema:.2f}  /  {sma:.2f}    "
          f"(EMA-SMA = {ema - sma:+.2f})")
    print(f"║  EMA slope        : {ema_slope:+.5f}    "
          f"({'rising' if ema_slope > 0 else 'falling'})")
    print(f"║  Momentum         : {momentum:+.4f}    "
          f"Δ = {momentum_delta:+.4f}    "
          f"({'positive' if momentum > 0 else 'negative'}, "
          f"{'re-accel' if momentum_delta > 0 else 'decel'})")
    print(f"║  Deviation        : {deviation:+.4f}    "
          f"vol_ratio = {_fmt(vol_ratio)}")
    print(f"║  ── Deterministic state (drives execution) ──")
    print(f"║  Structure        : {structure}")
    print(f"║  Regime           : {regime}")
    print(f"║  ── Institutional diagnostics ──")
    print(f"║  VWAP             : {_fmt(vwap)}    Close is {vwap_align} VWAP")
    print(f"║  RVOL             : {_fmt(rvol, '{:.3f}')}    "
          f"(diagnostic only — never gates entry)")
    print(f"║  ── HMM (informational) ──")
    if pd.notna(p_bull):
        print(f"║  P_bull / P_bear / P_range : "
              f"{p_bull:.2f} / {p_bear:.2f} / {p_range:.2f}")
    else:
        print(f"║  HMM not yet trained at this bar (warmup).")
    print(f"╠══════════════════════════════════════════════════════════════════════╣")
    print(f"║  EXECUTION STATE")
    print(f"║  Open stack depth : {open_now}    "
          f"(cap = {PULLBACK.max_pyramid_positions})")
    print(f"║  Exposure         : {current_exposure_pct:.1%} of equity    "
          f"(cap = {PULLBACK.capital_cap_pct:.0%})")
    if pyramid_ok:
        print(f"║  Pyramid allowed  : YES — bullish structure ✓ regime ✓ "
              f"VWAP ✓ momentum ✓")
    else:
        reason = ", ".join(pyr_blockers) if pyr_blockers else "none"
        print(f"║  Pyramid allowed  : no (blocked by: {reason})")
    print(f"╚══════════════════════════════════════════════════════════════════════╝")

    # --- Live signal evaluation ---
    print("\n  --- Live signal evaluation ---")
    if sig != 0:
        side = "LONG" if sig == 1 else "SHORT"
        prof = pullback_profile()
        sl = close * (1 - prof.stop_loss_pct) if sig == 1 else close * (1 + prof.stop_loss_pct)
        tp1 = close * (1 + prof.partial_tp_pct * sig)
        tp2 = close * (1 + prof.final_tp_pct * sig)
        notional = BACKTEST.initial_capital * PULLBACK.base_size_pct
        qty = notional / close
        print(f"    >>> PULLBACK {side} TRIGGERED <<<")
        print(f"        base notional ≈ ${notional:,.0f}  qty ≈ {qty:.2f}")
        print(f"        stop = {sl:.2f} ({-prof.stop_loss_pct:.1%})")
        print(f"        TP1  = {tp1:.2f} ({prof.partial_tp_pct:.1%}, "
              f"close {prof.partial_tp_size:.0%}, then trail to BE)")
        print(f"        TP2  = {tp2:.2f} ({prof.final_tp_pct:.1%}, runner)")
        if pyramid_ok and open_now > 0:
            print(f"        ↑ pyramid stack #{open_now + 1} permitted by VWAP/momentum/regime gate")
    else:
        bars_in_band = bool(last.get("Pullback", False))
        bars_imb = bool(last.get("Imbalance_long", False) or last.get("Imbalance_short", False))
        print(f"    no signal · in-band: {bars_in_band} · imbalance: {bars_imb} · "
              f"momentum re-accel: {momentum_delta > 0}")

    # --- Recent triggered signals tape ---
    recent = df[df["pullback_Signal"] != 0].tail(8)
    if not recent.empty:
        print("\n  --- Last 8 triggered signals ---")
        for ts_sig, row_sig in recent.iterrows():
            sig_v = int(row_sig["pullback_Signal"])
            side = "LONG " if sig_v == 1 else "SHORT"
            close_v = row_sig["Close"]
            vwap_v = row_sig.get("VWAP", float("nan"))
            vwap_marker = ("↑VWAP" if not pd.isna(vwap_v) and close_v > vwap_v
                           else "↓VWAP" if not pd.isna(vwap_v) else "?VWAP")
            rvol_v = row_sig.get("RVOL", float("nan"))
            print(f"    {ts_sig}  {side}  Close={close_v:.2f}  "
                  f"{vwap_marker}  RVOL={_fmt(rvol_v, '{:.2f}')}  "
                  f"regime={row_sig['Regime']}")


def main(argv: list[str]) -> None:
    refresh = "--refresh" in argv
    args = [a for a in argv if not a.startswith("--")]
    symbol = args[0] if args else DATA.symbols[0]
    render(symbol, refresh=refresh)


if __name__ == "__main__":
    main(sys.argv[1:])
